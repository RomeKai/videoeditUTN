"""
What a notification says about tokens is what happened to them, on real
reservations (reserved, then charged or returned by wallet_service):

- "nothing was charged" only when nothing of the project is charged or reserved;
- the refund it names is the one of that event, never an earlier one again;
- a clip rendered again that failed does not read as a success;
- a payment the person cancelled, or whose checkout expired, is not a failed charge;
- a database error while writing never undoes the money step that called it.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.notifications import services
from apps.notifications.models import Notification
from apps.payments.models import Payment, Transaction, Wallet
from apps.payments.services.payment_providers import Checkout, SimulatedProvider
from apps.payments.services.payments import CHECKOUT_FAILED, expire_checkout, fail_stale_checkouts, settle_payment
from apps.payments.services.wallet_service import commit_reservation, reserve_funds
from apps.payments.tests.billing_helpers import SIMULATED, client_for, owner_with_workspace
from apps.payments.tests.conftest import seeded_plans  # noqa: F401  (fixture)
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _record_pipeline_failure, finalize_project_render
from apps.videos.tests.test_ingestion_pipeline import fund_wallet

from .helpers import make_user, sent, team
from .test_events import DONE, DRAFT, FAILED, EventHarness

RENDER = 'Podcast 42: terminó el render'


class ChargedProjectTests(EventHarness):
    def with_clips(self, stage=PipelineStage.CLIPS_SELECTED):
        project = self.project(clips=[DRAFT, DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=stage)
        creation = self.reserve(project, '12.00')
        commit_reservation(creation.id)
        return project, creation

    def fail(self, project, stage=PipelineStage.CLIPS_SELECTED):
        self.committed(lambda: _record_pipeline_failure(project.id, RuntimeError('x'), 3, final=True, stage=stage))

    def test_a_failure_after_the_clips_exist_does_not_say_nothing_was_charged(self):
        project, creation = self.with_clips()

        self.fail(project)

        creation.refresh_from_db()
        self.assertEqual((creation.status, creation.amount), (Transaction.Status.COMPLETED, Decimal('12')))
        self.assert_everyone_got((
            'project_failed', 'Podcast 42 no se pudo procesar', 'Los clips que ya estaban siguen en el proyecto.',
        ))

    def test_charged_creation_and_returned_render_names_only_what_went_back(self):
        project, _ = self.with_clips(stage=PipelineStage.RENDER_DISPATCHED)
        clip_ids = [str(pk) for pk in VideoClip.objects.filter(project=project).values_list('pk', flat=True)]
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**project.metadata, 'auto_render_clip_ids': clip_ids, 'auto_render_seconds': 10.0},
        )
        project.refresh_from_db()
        self.reserve(project, '5.00', key='auto_render_reservation_id')

        self.fail(project, stage=PipelineStage.RENDER_DISPATCHED)

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', 'Te devolvimos 5 tokens.'))

    def test_a_reservation_that_could_not_be_returned_is_not_nothing_charged(self):
        """The refund failed (a database blip): the tokens are still set aside until the sweep returns them."""
        project = self.project(status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED)
        self.reserve(project, '12.00')

        self.committed(lambda: services.notify_project_failed(project.id, since=timezone.now()))

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', ''))

    def test_the_message_itself(self):
        message = services.project_failed_message
        self.assertEqual(message('P', 0)[1], 'No se cobró nada.')
        self.assertEqual(message('P', 0, holds_tokens=True)[1], '')
        self.assertEqual(message('P', 0, holds_tokens=True, has_clips=True)[1], 'Los clips que ya estaban siguen en el proyecto.')
        self.assertEqual(message('P', Decimal('1'), holds_tokens=True)[1], 'Te devolvimos 1 token.')
        self.assertEqual(
            message('P', Decimal('3'), reason=services.AUTO_RENDER_UNPAID),
            ('P: no alcanzó el saldo para renderizar', 'Los clips siguen ahí. Cargá tokens y volvé a intentarlo.'),
        )


class RefundOfThisEventTests(EventHarness):
    def partial(self):
        project = self.project(
            clips=[DONE, FAILED], status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        ids = [str(pk) for pk in VideoClip.objects.filter(project=project).order_by('start_time').values_list('pk', flat=True)]
        VideoProject.objects.filter(pk=project.pk).update(metadata={'auto_render_clip_ids': ids, 'auto_render_seconds': 10.0})
        project.refresh_from_db()
        self.reserve(project, '9.00', key='auto_render_reservation_id')
        self.committed(lambda: finalize_project_render(project.id))
        return project, ids

    def render_again(self, project, clip_id, amount='5.00', previous=None, scope=None, description=''):
        """
        What a paid render of one clip leaves (views_clips): reserved, back to
        render_dispatched. ``scope``: what it says it renders (``clip`` from
        the editor or a retry; none: reserved before the key existed).
        """
        project.refresh_from_db()
        txn = reserve_funds(self.wallet.id, Decimal(amount), project=project, description=description)
        metadata = {
            **project.metadata, 'clip_render_reservation_id': str(txn.id), 'clip_render_clip_ids': [clip_id],
            'clip_render_seconds': 5.0,
        }
        if scope:
            metadata['clip_render_scope'] = scope
        if previous:
            metadata['clip_render_previous'] = {clip_id: previous}
        VideoProject.objects.filter(pk=project.pk).update(
            metadata=metadata, pipeline_stage=PipelineStage.RENDER_DISPATCHED, status=VideoProject.Status.RENDERING,
        )
        return txn

    def bodies(self):
        return [body for _, _, body in sent()['ana']]

    def test_a_retry_that_fails_right_after_names_only_its_own_refund(self):
        project, ids = self.partial()
        self.render_again(project, ids[1])

        self.committed(lambda: finalize_project_render(project.id))

        self.assertEqual(self.bodies(), [
            'Parte del render falló. Te devolvimos 4,50 tokens.', 'Parte del render falló. Te devolvimos 5 tokens.',
        ])

    def test_a_retry_that_works_while_another_clip_is_still_failed_names_no_refund(self):
        project = self.project(
            clips=[DONE, FAILED, FAILED], status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        ids = [str(pk) for pk in VideoClip.objects.filter(project=project).order_by('start_time').values_list('pk', flat=True)]
        VideoProject.objects.filter(pk=project.pk).update(metadata={'auto_render_clip_ids': ids, 'auto_render_seconds': 15.0})
        project.refresh_from_db()
        self.reserve(project, '9.00', key='auto_render_reservation_id')
        self.committed(lambda: finalize_project_render(project.id))
        txn = self.render_again(project, ids[1])
        VideoClip.objects.filter(pk=ids[1]).update(status=DONE, s3_object_key='new-key')

        self.committed(lambda: finalize_project_render(project.id))

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.bodies(), ['Parte del render falló. Te devolvimos 6 tokens.', 'Parte del render falló.'])

    def test_a_clip_rendered_again_that_fails_does_not_read_as_a_success(self):
        project = self.project(
            clips=[DONE, DONE], status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        first, second = VideoClip.objects.filter(project=project).order_by('start_time')
        VideoClip.objects.filter(pk=first.pk).update(status=FAILED, s3_object_key='old-key')
        VideoClip.objects.filter(pk=second.pk).update(s3_object_key='other-key')
        txn = self.render_again(project, str(first.pk), previous={'status': 'completed', 'key': 'old-key'})

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.COMPLETED)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(set(VideoClip.objects.filter(project=project).values_list('status', flat=True)), {DONE})
        self.assert_everyone_got((
            'render_finished', RENDER,
            'No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban. Te devolvimos 5 tokens.',
        ))

    # --- the render of one clip (its editor, a retry): not the project's ---

    def delivered(self):
        """Two clips delivered and told: what a clip is rendered again over."""
        project = self.project(
            clips=[DONE, DONE], status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        first, second = VideoClip.objects.filter(project=project).order_by('start_time')
        VideoClip.objects.filter(pk=first.pk).update(s3_object_key='old-key')
        VideoClip.objects.filter(pk=second.pk).update(s3_object_key='other-key')
        self.committed(lambda: finalize_project_render(project.id))
        self.assertEqual(self.bodies(), ['2 clips listos.'])
        Notification.objects.all().delete()
        return project, first

    def activity_text(self, project, txn):
        """What Actividad says of the render ``txn`` paid."""
        from apps.videos.services.project_activity import _NAMESPACE, build_activity
        import uuid

        project.refresh_from_db()
        item_id = str(uuid.uuid5(_NAMESPACE, f'{project.pk}:render_finished:{txn.pk}'))
        (item,) = [item for item in build_activity(project) if item['id'] == item_id]
        return item['description']

    def test_one_clip_rendered_again_and_delivered_tells_nobody(self):
        project, first = self.delivered()
        txn = self.render_again(
            project, str(first.pk), previous={'status': 'completed', 'key': 'old-key'}, scope='clip',
            description='Render de clip: Clip 0',
        )
        VideoClip.objects.filter(pk=first.pk).update(status=DONE, s3_object_key='new-key')

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.COMPLETED)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        # Neither «2 clips listos» again nor an email: the project's render was told when it ended.
        self.assertEqual(sent(), {})
        self.assertEqual(self.activity_text(project, txn), 'Terminó el render de «Clip 0»')

    def test_one_clip_rendered_again_that_fails_says_what_the_activity_says(self):
        project, first = self.delivered()
        VideoClip.objects.filter(pk=first.pk).update(status=FAILED)
        txn = self.render_again(
            project, str(first.pk), amount='9.00', previous={'status': 'completed', 'key': 'old-key'}, scope='clip',
            description='Render de clip: Clip 0',
        )

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.COMPLETED)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        text = 'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 9 tokens.'
        self.assert_everyone_got(('render_finished', 'Podcast 42: no se pudo renderizar un clip', text))
        self.assertEqual(self.activity_text(project, txn), text)
        # Once: a second close tells nobody again.
        self.committed(lambda: finalize_project_render(project.id))
        self.assertEqual(Notification.objects.count(), 3)

    def test_a_retry_of_one_clip_that_fails_again_says_what_the_activity_says(self):
        project, ids = self.partial()
        Notification.objects.all().delete()
        txn = self.render_again(project, ids[1], amount='12.50', scope='clip', description='Reintento de clip: Clip 1')

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.PARTIAL)

        text = 'No se pudo renderizar el clip: sigue con error. Te devolvimos 12,50 tokens.'
        self.assert_everyone_got(('render_finished', 'Podcast 42: no se pudo renderizar un clip', text))
        self.assertEqual(self.activity_text(project, txn), text)

    def test_a_retry_of_one_clip_that_works_tells_nobody(self):
        project, ids = self.partial()
        Notification.objects.all().delete()
        self.render_again(project, ids[1], scope='clip', description='Reintento de clip: Clip 1')
        VideoClip.objects.filter(pk=ids[1]).update(status=DONE, s3_object_key='new-key')

        self.assertEqual(self.committed(lambda: finalize_project_render(project.id)), PipelineStage.COMPLETED)

        self.assertEqual(sent(), {})

    def test_every_clip_rendered_again_is_still_told_as_the_projects_render(self):
        project, first = self.delivered()
        VideoClip.objects.filter(pk=first.pk).update(status=DONE, s3_object_key='new-key')
        self.render_again(
            project, str(first.pk), previous={'status': 'completed', 'key': 'old-key'}, scope='project',
            description='Render con otro estilo: Podcast 42',
        )

        self.committed(lambda: finalize_project_render(project.id))

        self.assert_everyone_got(('render_finished', RENDER, '2 clips listos.'))

    def test_the_notice_of_one_clip_follows_the_rules_of_every_notice(self):
        def failing_clip_render():
            project, first = self.delivered()
            VideoClip.objects.filter(pk=first.pk).update(status=FAILED)
            self.render_again(project, str(first.pk), previous={'status': 'completed', 'key': 'old-key'}, scope='clip')
            return project

        # Rolled back with the close: nobody is told.
        project = failing_clip_render()
        self.rolled_back(lambda: finalize_project_render(project.id))
        self.assertEqual(sent(), {})

        # A bell that fails never stops the close or the refund.
        project = failing_clip_render()
        stage = self.assert_survives_a_broken_bell(lambda: finalize_project_render(project.id))
        self.assertEqual(stage, PipelineStage.COMPLETED)
        refunded = Transaction.objects.get(pk=VideoProject.objects.get(pk=project.pk).metadata['clip_render_reservation_id'])
        self.assertEqual(refunded.status, Transaction.Status.FAILED)

        # Only the newest KEPT_PER_USER of each user stay.
        fund_wallet(self.workspace, '500.00')
        projects = [failing_clip_render() for _ in range(services.KEPT_PER_USER + 2)]
        for project in projects:
            self.committed(lambda: finalize_project_render(project.id))
        self.assertEqual(Notification.objects.filter(user=self.ana).count(), services.KEPT_PER_USER)

    def test_a_failure_recorded_again_later_does_not_move_the_refund(self):
        """pipeline_error_at is rewritten by a later record_error: the refund is still the event's own."""
        project = self.project(status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.TRANSCRIBED)
        self.reserve(project, '12.50')
        with self.captureOnCommitCallbacks(execute=True):
            _record_pipeline_failure(project.id, RuntimeError('x'), 1, final=True, stage=PipelineStage.TRANSCRIBED)
            VideoProject.objects.filter(pk=project.pk).update(pipeline_error_at=timezone.now() + timedelta(hours=1))

        self.assert_everyone_got(('project_failed', 'Podcast 42 no se pudo procesar', 'Te devolvimos 12,50 tokens.'))


class WriteOrderTests(EventHarness):
    def test_the_recipients_are_written_in_one_order(self):
        created = []
        real = Notification.objects.create

        def create(**fields):
            created.append(fields['user_id'])
            return real(**fields)

        with patch('apps.notifications.services.Notification.objects.create', side_effect=create):
            services.write(self.workspace, {self.vera.pk, self.ana.pk, self.edu.pk}, 'project_ready', 'T', 'B')

        self.assertEqual(created, sorted([self.ana.pk, self.edu.pk, self.vera.pk]))


class DatabaseErrorTests(TransactionTestCase):
    """Real commits: what the caller wrote is in the database although the notification's insert failed there."""

    def setUp(self):
        self.ana = make_user('ana')
        self.workspace = team(self.ana)
        self.wallet = fund_wallet(self.workspace, '100.00')

    def broken_insert(self):
        """The insert reaches the database and fails there (NOT NULL), inside the service's own transaction."""
        real = Notification.objects.create

        def create(**fields):
            return real(**{**fields, 'title': None})

        return patch('apps.notifications.services.Notification.objects.create', side_effect=create)

    def test_an_approved_purchase_is_credited_although_the_insert_fails(self):
        payment = Payment.objects.create(
            workspace=self.workspace, created_by=self.ana, tokens=1500, amount_usd=Decimal('15.00'), provider='simulated',
        )

        with self.broken_insert() as create, self.assertLogs('apps.notifications.services', logging.ERROR) as logs:
            _, changed = settle_payment(payment.pk, approved=True)

        create.assert_called_once()
        self.assertTrue(changed)
        self.assertIn(IntegrityError.__name__, logs.output[0])
        self.assertFalse(connection.needs_rollback)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('1600'))
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_database_error_before_the_savepoint_does_not_undo_the_credit(self):
        """
        Only on_commit (services._after_commit) covers this one: the error comes
        before the notification's own savepoint, in ``recipients_of``. Written
        in the caller's transaction, it would leave that transaction aborted and
        the commit would roll the credit back, with the payment still pending.
        """
        payment = Payment.objects.create(
            workspace=self.workspace, created_by=self.ana, tokens=1500, amount_usd=Decimal('15.00'), provider='simulated',
        )

        def refused_query(*args):
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM una_tabla_que_no_existe')

        with patch('apps.notifications.services.recipients_of', side_effect=refused_query), \
                self.assertLogs('apps.notifications.services', logging.ERROR) as logs:
            _, changed = settle_payment(payment.pk, approved=True)

        self.assertTrue(changed)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('1600'))
        credit = Transaction.objects.get(pk=payment.credit_transaction_id)
        self.assertEqual((credit.status, credit.amount), (Transaction.Status.COMPLETED, Decimal('1500')))
        self.assertEqual(Notification.objects.count(), 0)
        # Ids and the class of the error, never its text.
        self.assertEqual(logs.output, [
            'ERROR:apps.notifications.services:notifications.not_created '
            f'event=payment_settled payment_id={payment.pk} error=ProgrammingError',
        ])

    def test_a_failed_project_is_refunded_although_the_prune_fails(self):
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.ana, title='Podcast 42', source_file='x/y.mp4',
            status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.TRANSCRIBED,
        )
        # Above the "saldo bajo" threshold: the reservation writes no notification of its own.
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal('1000'))
        txn = reserve_funds(self.wallet.id, Decimal('12.50'), project=project)
        VideoProject.objects.filter(pk=project.pk).update(metadata={'reservation_id': str(txn.id)})

        def prune(*args):
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM una_tabla_que_no_existe')

        with patch('apps.notifications.services._prune', side_effect=prune), \
                self.assertLogs('apps.notifications.services', logging.ERROR):
            _record_pipeline_failure(project.id, RuntimeError('x'), 1, final=True, stage=PipelineStage.TRANSCRIBED)

        txn.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual((project.status, txn.status), (VideoProject.Status.FAILED, Transaction.Status.FAILED))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('1000'))
        # The insert went back with the failed prune: nothing half written.
        self.assertEqual(Notification.objects.count(), 0)


CHANGE = '/api/v1/subscription/change/'


@pytest.fixture
def owner(seeded_plans):  # noqa: F811
    return owner_with_workspace()


@pytest.mark.django_db
class TestPaymentsNobodyRefused:
    @pytest.fixture(autouse=True)
    def simulated_provider(self, settings):
        for name, value in SIMULATED.items():
            setattr(settings, name, value)

    def change(self, user, plan, capture):
        with capture(execute=True):
            return client_for(user).post(CHANGE, {'plan_id': str(plan.pk)}, format='json')

    def test_keeping_the_plan_before_paying_is_not_a_failed_charge(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        user, workspace = owner
        self.change(user, seeded_plans['Pro'], django_capture_on_commit_callbacks)

        response = self.change(user, seeded_plans['Gratis'], django_capture_on_commit_callbacks)

        assert response.status_code == 200
        assert Payment.objects.get(workspace=workspace).status == Payment.Status.REJECTED
        assert not Notification.objects.exists()

    def test_choosing_another_plan_before_paying_is_not_a_failed_charge(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        user, workspace = owner
        self.change(user, seeded_plans['Pro'], django_capture_on_commit_callbacks)

        self.change(user, seeded_plans['Creador'], django_capture_on_commit_callbacks)

        assert sorted(Payment.objects.filter(workspace=workspace).values_list('status', flat=True)) == ['pending', 'rejected']
        assert not Notification.objects.exists()

    def test_an_expired_checkout_is_not_a_failed_charge(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        user, workspace = owner
        self.change(user, seeded_plans['Pro'], django_capture_on_commit_callbacks)
        Payment.objects.filter(workspace=workspace).update(checkout_started_at=timezone.now() - timedelta(days=2))

        with django_capture_on_commit_callbacks(execute=True):
            _, expired = expire_checkout(Payment.objects.get(workspace=workspace))

        assert expired
        assert not Notification.objects.exists()

    def test_a_checkout_the_provider_did_not_open_is_not_a_failed_charge(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        """_fail_checkout: the provider failed before anyone could pay, so "we could not charge you" would be false."""
        user, workspace = owner
        with patch.object(SimulatedProvider, 'start_checkout', side_effect=RuntimeError('proveedor caído')):
            response = self.change(user, seeded_plans['Pro'], django_capture_on_commit_callbacks)

        assert (response.status_code, response.json()['code']) == (502, 'checkout_failed')
        payment = Payment.objects.get(workspace=workspace)
        assert (payment.status, payment.failure_reason, payment.created_by_id) == (Payment.Status.REJECTED, CHECKOUT_FAILED, user.pk)
        assert not Notification.objects.exists()

    def test_a_checkout_too_long_to_keep_is_not_a_failed_charge(self, owner, django_capture_on_commit_callbacks):
        user, workspace = owner
        huge = Checkout(url=f"http://localhost:3000/dev/checkout?x={'a' * 2100}", reference='sim_huge')
        with patch.object(SimulatedProvider, 'start_checkout', return_value=huge), \
                django_capture_on_commit_callbacks(execute=True):
            response = client_for(user).post(
                '/api/v1/token-purchases/', {'package_id': 'pack-1000', 'return_url': 'http://localhost:3000/uso/comprar/resultado'},
                format='json',
            )

        assert response.status_code == 502
        assert Payment.objects.get(workspace=workspace).status == Payment.Status.REJECTED
        assert not Notification.objects.exists()

    def test_an_attempt_whose_checkout_never_opened_is_not_a_failed_charge(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        user, workspace = owner
        long_ago = timezone.now() - timedelta(hours=1)
        lost = Payment.objects.create(
            workspace=workspace, created_by=user, plan=seeded_plans['Pro'], amount_usd=Decimal('49.00'), provider='simulated',
        )
        Payment.objects.filter(pk=lost.pk).update(created_at=long_ago, checkout_started_at=long_ago)

        with django_capture_on_commit_callbacks(execute=True):
            assert fail_stale_checkouts() == 1

        lost.refresh_from_db()
        assert (lost.status, lost.failure_reason) == (Payment.Status.REJECTED, CHECKOUT_FAILED)
        assert not Notification.objects.exists()

    def test_a_payment_the_provider_refused_still_tells_who_started_it(self, owner, seeded_plans, django_capture_on_commit_callbacks):  # noqa: F811
        user, workspace = owner
        self.change(user, seeded_plans['Pro'], django_capture_on_commit_callbacks)

        with django_capture_on_commit_callbacks(execute=True):
            settle_payment(Payment.objects.get(workspace=workspace).pk, approved=False)

        assert list(Notification.objects.values_list('kind', 'title')) == [('payment_rejected', 'No pudimos cobrar el plan Pro')]
