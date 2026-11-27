"""
Each event writes its notification for the right people, only once its change
is committed, and never breaks the step it happens in:

- ready for review (``_hand_to_review``), render finished
  (``finalize_project_render``), failed (the pipeline, the final render, the
  stuck-ingestion sweep, the create view when the broker refuses the message);
- a payment approved or rejected (``settle_payment``).
"""
import logging
from decimal import Decimal
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from apps.notifications.models import Notification
from apps.payments.models import Payment, SubscriptionPlan, Transaction, Wallet
from apps.payments.services.payments import settle_payment
from apps.payments.services.wallet_service import reserve_funds, rollback_reservation
from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _record_pipeline_failure, finalize_project_render
from apps.videos.tasks.ingestion import _hand_to_review
from apps.videos.tasks.render import _fail_render
from apps.videos.tasks.render_recovery import recover_stuck_renders_now
from apps.videos.services.ai.errors import NonRetryableAIError
from apps.videos.tasks import render_video_segments
from apps.videos.tests.test_approved_segments_source import SEGMENTS, _patched_render, _render_mocks
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result
from apps.videos.tests.test_ingestion_recovery import StuckIngestionHarness
from apps.videos.views import _fail_unqueued_project

from .helpers import PENDING, VIEWER, BellIsBroken, broken_bell, join, make_user, sent, team

DONE, FAILED, DRAFT = VideoClip.Status.COMPLETED, VideoClip.Status.FAILED, VideoClip.Status.DRAFT


class Rollback(Exception):
    pass


class EventHarness(TestCase):
    """
    A team: ``ana`` owns it, ``edu`` (who uploads) and ``vera`` (Lector) are
    members, ``ivan`` is only invited and ``otto`` is from elsewhere.
    """

    def setUp(self):
        self.ana, self.edu, self.vera = make_user('ana'), make_user('edu'), make_user('vera')
        self.ivan, self.otto = make_user('ivan'), make_user('otto')
        self.workspace = team(self.ana)
        join(self.workspace, self.edu)
        join(self.workspace, self.vera, VIEWER)
        join(self.workspace, self.ivan, status=PENDING)
        self.wallet = fund_wallet(self.workspace, '100.00')

    def project(self, clips=(), **fields):
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.edu, title='Podcast 42', source_file='x/y.mp4', **fields,
        )
        for index, status in enumerate(clips):
            VideoClip.objects.create(
                project=project, title=f'Clip {index}', start_time=index * 10, end_time=index * 10 + 5, status=status,
            )
        return project

    def reserve(self, project, amount, key='reservation_id'):
        txn = reserve_funds(self.wallet.id, Decimal(amount), project=project)
        VideoProject.objects.filter(pk=project.pk).update(metadata={**(project.metadata or {}), key: str(txn.id)})
        project.refresh_from_db()
        return txn

    def committed(self, event):
        """Runs ``event`` and what it left for after the commit."""
        with self.captureOnCommitCallbacks(execute=True):
            return event()

    def rolled_back(self, event):
        """Runs ``event`` inside a transaction that is rolled back."""
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    event()
                    raise Rollback()
            except Rollback:
                pass

    def assert_everyone_got(self, expected):
        self.assertEqual(sent(), {'ana': [expected], 'edu': [expected], 'vera': [expected]})

    def assert_survives_a_broken_bell(self, event):
        """The event's own result is the same; the log has the class of the error, never its text."""
        with broken_bell() as create, self.assertLogs('apps.notifications.services', logging.ERROR) as logs:
            result = self.committed(event)
        create.assert_called()
        self.assertEqual(Notification.objects.count(), 0)
        self.assertIn(BellIsBroken.__name__, logs.output[0])
        self.assertNotIn('texto', ' '.join(logs.output))
        return result


class ReadyForReviewTests(EventHarness):
    def waiting(self):
        return self.project(
            clips=[DRAFT, DRAFT, DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        )

    def test_every_member_is_told_with_the_number_of_clips(self):
        project = self.waiting()

        self.assertTrue(self.committed(lambda: _hand_to_review(project.id)))

        self.assert_everyone_got(('project_ready', 'Podcast 42 está listo para revisar', 'Encontramos 3 clips.'))
        self.assertEqual(set(Notification.objects.values_list('project_id', flat=True)), {project.id})

    def test_a_project_that_did_not_move_notifies_nobody(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED)

        self.assertFalse(self.committed(lambda: _hand_to_review(project.id)))

        self.assertEqual(sent(), {})

    def test_rolled_back_it_notifies_nobody(self):
        project = self.waiting()

        self.rolled_back(lambda: _hand_to_review(project.id))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.INGESTING)
        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_handoff(self):
        project = self.waiting()

        self.assertTrue(self.assert_survives_a_broken_bell(lambda: _hand_to_review(project.id)))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)


class RenderFinishedTests(EventHarness):
    def rendering(self, *clips, reserved=None):
        project = self.project(
            clips=clips, status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        if reserved:
            clip_ids = [str(pk) for pk in VideoClip.objects.filter(project=project).values_list('pk', flat=True)]
            VideoProject.objects.filter(pk=project.pk).update(
                metadata={'auto_render_clip_ids': clip_ids, 'auto_render_seconds': 5.0 * len(clips)},
            )
            project.refresh_from_db()
            self.reserve(project, reserved, key='auto_render_reservation_id')
        return project

    def test_every_clip_delivered(self):
        project = self.rendering(DONE, DONE, DONE)

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.COMPLETED)

        self.assert_everyone_got(('render_finished', 'Podcast 42: terminó el render', '3 clips listos.'))

    def test_a_single_video(self):
        project = self.rendering(DONE)

        self.committed(lambda: finalize_project_render(project.id))

        self.assert_everyone_got(('render_finished', 'Podcast 42: terminó el render', 'Tu video está listo.'))

    def test_a_partial_render_says_what_was_returned(self):
        project = self.rendering(DONE, FAILED, reserved='9.00')

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.PARTIAL)

        txn = Transaction.objects.get(pk=project.metadata['auto_render_reservation_id'])
        self.assertEqual((txn.amount, txn.reserved_amount), (Decimal('4.5'), Decimal('9')))
        self.assert_everyone_got((
            'render_finished', 'Podcast 42: terminó el render', 'Parte del render falló. Te devolvimos 4,50 tokens.',
        ))

    def test_a_render_still_running_notifies_nobody_and_closing_it_twice_notifies_once(self):
        project = self.rendering(DONE, DRAFT)

        self.assertIsNone(self.committed(lambda: finalize_project_render(project.id)))
        self.assertEqual(sent(), {})

        VideoClip.objects.filter(project=project).update(status=DONE)
        self.committed(lambda: finalize_project_render(project.id))
        self.committed(lambda: finalize_project_render(project.id))

        self.assertEqual(Notification.objects.count(), 3)

    def test_rolled_back_it_notifies_nobody(self):
        project = self.rendering(DONE)

        self.rolled_back(lambda: finalize_project_render(project.id))

        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_close(self):
        project = self.rendering(DONE, FAILED, reserved='9.00')

        stage = self.assert_survives_a_broken_bell(lambda: finalize_project_render(project.id))

        self.assertEqual(stage, PipelineStage.PARTIAL)
        self.assertEqual(Transaction.objects.get(project=project).status, Transaction.Status.COMPLETED)


class PipelineFailedTests(EventHarness):
    def ingesting(self):
        project = self.project(status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.TRANSCRIBED)
        self.reserve(project, '12.50')
        return project

    def fail(self, project, final=True):
        return _record_pipeline_failure(project.id, RuntimeError('x'), 1, final=final, stage=PipelineStage.TRANSCRIBED)

    def test_the_final_failure_says_what_was_returned(self):
        project = self.ingesting()

        self.committed(lambda: self.fail(project))

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', 'Te devolvimos 12,50 tokens.'))

    def test_an_attempt_that_will_be_retried_notifies_nobody(self):
        project = self.ingesting()

        self.committed(lambda: self.fail(project, final=False))

        self.assertEqual(sent(), {})

    def test_a_project_another_worker_moved_on_notifies_nobody(self):
        project = self.ingesting()
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.MODERATED)

        self.committed(lambda: self.fail(project))

        self.assertEqual(sent(), {})

    def test_a_refund_of_an_earlier_failure_is_not_counted_again(self):
        project = self.ingesting()
        self.committed(lambda: self.fail(project))
        Notification.objects.all().delete()
        # Dispatched again, free, and failed again right away: nothing new went back.
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.TRANSCRIBED,
        )

        self.committed(lambda: self.fail(project))

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', 'No se cobró nada.'))

    def test_rolled_back_it_notifies_nobody(self):
        project = self.ingesting()

        self.rolled_back(lambda: self.fail(project))

        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_refund(self):
        project = self.ingesting()

        self.assert_survives_a_broken_bell(lambda: self.fail(project))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(Transaction.objects.get(project=project).status, Transaction.Status.FAILED)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('100'))


class FinalRenderFailedTests(EventHarness):
    def rendering(self):
        project = self.project(status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.CLIPS_SELECTED)
        self.reserve(project, '20.00', key='render_reservation_id')
        return project

    def test_it_says_what_was_returned_once(self):
        project = self.rendering()

        self.committed(lambda: _fail_render(project.id, 'render_failed'))
        # A second copy of the delivery fails it again: nothing new.
        self.committed(lambda: _fail_render(project.id, 'render_failed'))

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo generar', 'Te devolvimos 20 tokens.'))

    def test_a_delivered_render_is_not_failed_nor_notified(self):
        project = self.rendering()
        VideoProject.objects.filter(pk=project.pk).update(status=VideoProject.Status.COMPLETED, final_export_r2_key='k')

        self.committed(lambda: _fail_render(project.id, 'render_failed'))

        self.assertEqual(sent(), {})

    def test_rolled_back_it_notifies_nobody(self):
        project = self.rendering()

        self.rolled_back(lambda: _fail_render(project.id, 'render_failed'))

        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_refund(self):
        project = self.rendering()

        self.assert_survives_a_broken_bell(lambda: _fail_render(project.id, 'render_failed'))

        project.refresh_from_db()
        self.assertEqual((project.status, project.pipeline_error_code), (VideoProject.Status.FAILED, 'render_failed'))
        self.assertEqual(Transaction.objects.get(project=project).status, Transaction.Status.FAILED)


class PaperEditTests(EventHarness):
    def approved(self, **fields):
        return self.project(
            status=VideoProject.Status.RENDERING, original_r2_key='videos/1/original/source.mp4', add_subtitles=False,
            **fields,
        )

    def render(self, project):
        source, processed = _render_mocks()
        with _patched_render(source, processed), self.captureOnCommitCallbacks(execute=True):
            return render_video_segments(project.id)

    def test_the_delivered_video_is_announced_once(self):
        project = self.approved(approved_segments=SEGMENTS)

        self.render(project)
        # A second copy of the message finds it delivered.
        self.render(project)

        self.assert_everyone_got(('render_finished', 'Podcast 42: terminó el render', 'Tu video está listo.'))

    def test_one_that_cannot_be_rendered_is_announced_as_failed(self):
        project = self.approved()

        with self.assertRaises(NonRetryableAIError):
            self.render(project)

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo generar', 'No se cobró nada.'))

    def test_a_failing_notification_does_not_fail_the_render(self):
        project = self.approved(approved_segments=SEGMENTS)

        with broken_bell(), self.assertLogs('apps.notifications.services', logging.ERROR):
            result = self.render(project)

        project.refresh_from_db()
        self.assertIn('Render Success', result)
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)


class UnpaidAutoRenderTests(IngestionHarness, TestCase):
    def test_a_re_dispatch_that_cannot_be_paid_says_so_and_never_that_nothing_was_charged(self):
        wallet = fund_wallet(self.workspace, '100.00')
        self.mock_select.return_value = selection_result(count=3)
        project = self.make_project(auto_render_bypass=True)
        creation = reserve_funds(wallet.id, Decimal('10.00'), project=project)
        VideoProject.objects.filter(pk=project.pk).update(metadata={'reservation_id': str(creation.id)})
        self.run_task(project)
        project.refresh_from_db()
        rollback_reservation(project.metadata['auto_render_reservation_id'])
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal('1.00'))
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.RENDER_DISPATCHED, status=VideoProject.Status.RENDERING,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        project.refresh_from_db()
        creation.refresh_from_db()
        self.assertEqual(project.pipeline_error_code, 'AutoRenderNotPaid')
        # The creation stays charged and the clips are there.
        self.assertEqual((creation.status, creation.amount), (Transaction.Status.COMPLETED, Decimal('10')))
        self.assertEqual(VideoClip.objects.filter(project=project).count(), 3)
        self.assertEqual(sent(), {'pipe': [(
            'project_failed', 'Pipeline: no alcanzó el saldo para renderizar',
            'Los clips siguen ahí. Cargá tokens y volvé a intentarlo.',
        )]})


class NotQueuedTests(EventHarness):
    def created(self):
        project = self.project(status=VideoProject.Status.UPLOADED, pipeline_stage=PipelineStage.UPLOADED)
        return project, self.reserve(project, '10.00')

    def test_a_project_the_broker_refused_says_what_was_returned(self):
        project, txn = self.created()

        self.assertEqual(self.committed(lambda: _fail_unqueued_project(project.id, txn.id)), 'refunded')

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', 'Te devolvimos 10 tokens.'))

    def test_a_refund_that_cannot_be_written_notifies_nobody(self):
        project, txn = self.created()

        with patch('apps.payments.services.wallet_service.rollback_reservation', side_effect=RuntimeError('x')):
            self.committed(lambda: _fail_unqueued_project(project.id, txn.id))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.UPLOADED)
        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_refund(self):
        project, txn = self.created()

        result = self.assert_survives_a_broken_bell(lambda: _fail_unqueued_project(project.id, txn.id))

        self.assertEqual(result, 'refunded')
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)


class StuckIngestionTests(StuckIngestionHarness, TestCase):
    def stuck_for_good(self):
        project, txn = self.lost(idle=None)
        self.spend_requeues(project)
        self.age(project, render_recovery_fail_after() + 60)
        return project, txn

    def test_the_sweep_that_fails_it_says_what_was_returned(self):
        project, txn = self.stuck_for_good()

        with self.captureOnCommitCallbacks(execute=True):
            row, _ = self.sweep(project)

        project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual((row['action'], project.status, txn.status), ('fail', 'failed', Transaction.Status.FAILED))
        self.assertEqual(sent(), {'pipe': [('project_failed', 'Pipeline no se pudo procesar', 'Te devolvimos 10 tokens.')]})

    def test_a_failing_notification_does_not_stop_the_sweep(self):
        project, txn = self.stuck_for_good()

        with broken_bell(), self.assertLogs('apps.notifications.services', logging.ERROR), \
                self.captureOnCommitCallbacks(execute=True):
            row, _ = self.sweep(project)

        txn.refresh_from_db()
        self.assertEqual((row['action'], txn.status), ('fail', Transaction.Status.FAILED))
        self.assertEqual(Notification.objects.count(), 0)


def render_recovery_fail_after():
    from apps.videos.tasks import render_recovery

    return render_recovery.ingestion_fail_after()


class PaymentTests(EventHarness):
    def purchase(self, by, tokens=1500, **fields):
        return Payment.objects.create(
            workspace=self.workspace, created_by=by, tokens=tokens, amount_usd=Decimal('15.00'), provider='simulated',
            **fields,
        )

    def upgrade(self, by):
        plan = SubscriptionPlan.objects.create(name='Plan Grande', max_resolution='1080p', max_members=10)
        return self.purchase(by, tokens=0, plan=plan)

    def settle(self, payment, approved=True):
        return self.committed(lambda: settle_payment(payment.pk, approved=approved))

    def test_an_approved_purchase_tells_who_bought(self):
        self.settle(self.purchase(self.ana))

        self.assertEqual(sent(), {'ana': [('payment_approved', 'Se acreditaron 1.500 tokens', 'Tu compra se aprobó.')]})
        self.assertIsNone(Notification.objects.get().project_id)

    def test_a_rejected_purchase(self):
        self.settle(self.purchase(self.ana), approved=False)

        self.assertEqual(sent(), {'ana': [(
            'payment_rejected', 'No pudimos cobrar tu compra de 1.500 tokens', 'No se cobró nada. Podés intentarlo de nuevo.',
        )]})

    def test_an_approved_plan(self):
        self.settle(self.upgrade(self.ana))

        self.assertEqual(sent(), {'ana': [('payment_approved', 'Tu plan ahora es Plan Grande', 'El pago se aprobó.')]})

    def test_a_rejected_plan(self):
        self.settle(self.upgrade(self.ana), approved=False)

        self.assertEqual(sent(), {'ana': [(
            'payment_rejected', 'No pudimos cobrar el plan Plan Grande', 'No se cobró nada. Podés intentarlo de nuevo.',
        )]})

    def test_a_second_confirmation_notifies_nobody(self):
        payment = self.purchase(self.ana)
        self.settle(payment)

        self.assertFalse(self.settle(payment)[1])

        self.assertEqual(Notification.objects.count(), 1)

    def test_who_left_the_workspace_is_not_told(self):
        payment = self.purchase(self.otto)

        self.settle(payment)

        self.assertEqual(sent(), {})

    def test_a_payment_of_nobody_notifies_nobody(self):
        self.settle(self.purchase(None))

        self.assertEqual(sent(), {})

    def test_rolled_back_it_notifies_nobody(self):
        payment = self.purchase(self.ana)

        self.rolled_back(lambda: settle_payment(payment.pk, approved=True))

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(sent(), {})

    def test_a_failing_notification_does_not_stop_the_credit(self):
        payment = self.purchase(self.ana)

        _, changed = self.assert_survives_a_broken_bell(lambda: settle_payment(payment.pk, approved=True))

        self.assertTrue(changed)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('1600'))


class RecipientsTests(EventHarness):
    def test_only_the_owner_and_the_active_members_of_that_workspace(self):
        other = team(self.otto, 'Otro')
        project = self.project(
            clips=[DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        )

        self.committed(lambda: _hand_to_review(project.id))

        self.assertEqual(set(sent(self.workspace)), {'ana', 'edu', 'vera'})
        self.assertEqual(sent(other), {})
        self.assertEqual(sent()['edu'], [('project_ready', 'Podcast 42 está listo para revisar', 'Encontramos 1 clip.')])

    def test_a_member_removed_before_the_commit_is_not_told(self):
        from apps.users.models import WorkspaceMember

        project = self.project(
            clips=[DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            _hand_to_review(project.id)
            WorkspaceMember.objects.filter(workspace=self.workspace, user=self.edu).delete()

        self.assertEqual(set(sent()), {'ana', 'vera'})

    def test_a_deleted_project_keeps_its_notification_without_the_link(self):
        project = self.project(
            clips=[DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        )
        self.committed(lambda: _hand_to_review(project.id))

        VideoProject.objects.filter(pk=project.pk).delete()

        self.assertEqual(list(Notification.objects.values_list('project_id', flat=True)), [None, None, None])
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())
