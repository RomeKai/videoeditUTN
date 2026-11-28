"""
The emails of the notifications (apps/notifications/emails.py and tasks.py):

- each event of a kind with an email sends exactly one to each recipient of
  the bell who has that kind on, and none to who turned it off, has no
  verified email or is not in the workspace;
- nothing is sent when the event is rolled back;
- a send that fails, or a queue that refuses the message, changes neither the
  event nor the bell, and the log has ids and the class of the error only;
- no email carries text a user typed;
- a payment and the demo seed's examples send no email.
"""
import logging
import smtplib
import threading
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.notifications import services
from apps.notifications.emails import EMAIL_KINDS, build
from apps.notifications.models import Notification
from apps.notifications.tasks import send_notification_email
from apps.payments.models import Payment, Transaction
from apps.payments.services.payments import settle_payment
from apps.payments.services.wallet_service import reserve_funds
from apps.users.models import NotificationPreference, User, Workspace, WorkspaceMember
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _record_pipeline_failure, finalize_project_render
from apps.videos.tasks.ingestion import _hand_to_review

from .helpers import make_user, sent, team
from .test_events import DONE, DRAFT, FAILED, EventHarness

SEND = 'apps.notifications.emails.EmailMultiAlternatives.send'
DELAY = 'apps.notifications.tasks.send_notification_email.delay'
TYPED = ('<b>Título & secreto</b>', 'Estudio <i>Norte</i>', 'Anabella', 'Quiroga')


def whole(message):
    """Everything an email carries: subject, text and HTML."""
    return '\n'.join([message.subject, message.body, *(content for content, _ in message.alternatives)])


@override_settings(FRONTEND_URL='https://app.onecreator.test/')
class EmailHarness(EventHarness):
    """The team of EventHarness with verified emails. ``vera`` turned every email off."""

    def setUp(self):
        super().setUp()
        User.objects.update(is_email_verified=True)
        NotificationPreference.objects.create(
            user=self.vera, project_ready=False, render_finished=False, project_failed=False, low_balance=False,
        )

    def assert_one_email_each(self, *usernames):
        self.assertEqual(sorted(address for message in mail.outbox for address in message.to),
                         sorted(f'{name}@test.com' for name in usernames))
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertEqual([mimetype for _, mimetype in message.alternatives], ['text/html'])
        return mail.outbox[0] if mail.outbox else None

    def waiting(self):
        return self.project(
            clips=[DRAFT, DRAFT, DRAFT], status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        )

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

    def ingesting(self):
        project = self.project(status=VideoProject.Status.INGESTING, pipeline_stage=PipelineStage.TRANSCRIBED)
        self.reserve(project, '12.50')
        return project

    def fail_project(self, project):
        return self.committed(lambda: _record_pipeline_failure(project.id, RuntimeError('x'), 1, final=True, stage=PipelineStage.TRANSCRIBED))


class EventEmailTests(EmailHarness):
    def test_ready_for_review(self):
        project = self.waiting()

        self.committed(lambda: _hand_to_review(project.id))

        message = self.assert_one_email_each('ana', 'edu')
        self.assertEqual(message.subject, 'Tu proyecto está listo para revisar')
        self.assertIn('Encontramos 3 clips.', message.body)
        link = f'https://app.onecreator.test/proyectos/{project.id}/revision?espacio={self.workspace.id}'
        self.assertIn(link, message.body)
        self.assertIn(f'href="{link}"', message.alternatives[0][0])
        self.assertIn('https://app.onecreator.test/perfil/notificaciones\n', message.body)
        self.assertIn('tenés activado el aviso «El proyecto está listo para revisar»', message.body)

    def test_render_finished(self):
        project = self.rendering(DONE, DONE)

        self.committed(lambda: finalize_project_render(project.id))

        message = self.assert_one_email_each('ana', 'edu')
        self.assertEqual(message.subject, 'Terminó el render de tu proyecto')
        self.assertIn('2 clips listos.', message.body)
        self.assertIn(f'https://app.onecreator.test/proyectos/{project.id}/clips?espacio={self.workspace.id}\n', message.body)

    def test_a_partial_render_says_what_was_returned_like_the_bell(self):
        project = self.rendering(DONE, FAILED, reserved='9.00')

        self.committed(lambda: finalize_project_render(project.id))

        message = self.assert_one_email_each('ana', 'edu')
        self.assertIn('Parte del render falló. Te devolvimos 4,50 tokens.', message.body)
        self.assertIn('Parte del render falló. Te devolvimos 4,50 tokens.', message.alternatives[0][0])

    def test_a_failed_project_says_what_was_returned_like_the_bell(self):
        project = self.ingesting()

        self.fail_project(project)

        message = self.assert_one_email_each('ana', 'edu')
        self.assertEqual(message.subject, 'No pudimos terminar tu proyecto')
        self.assertEqual(Notification.objects.filter(user=self.ana).get().body, 'Te devolvimos 12,50 tokens.')
        self.assertIn('Te devolvimos 12,50 tokens.', message.body)
        self.assertIn(f'https://app.onecreator.test/proyectos/{project.id}?espacio={self.workspace.id}\n', message.body)

    def test_each_switch_only_turns_its_own_kind_off(self):
        NotificationPreference.objects.create(user=self.edu, project_ready=False)
        ready, failing = self.waiting(), self.ingesting()

        self.committed(lambda: _hand_to_review(ready.id))
        self.assert_one_email_each('ana')
        mail.outbox.clear()
        self.fail_project(failing)

        self.assert_one_email_each('ana', 'edu')

    def test_who_turned_it_off_still_gets_the_bell(self):
        project = self.waiting()

        self.committed(lambda: _hand_to_review(project.id))

        self.assertEqual(set(sent()), {'ana', 'edu', 'vera'})

    def test_rolled_back_nothing_is_sent(self):
        ready, failing = self.waiting(), self.ingesting()

        self.rolled_back(lambda: _hand_to_review(ready.id))
        self.rolled_back(lambda: _record_pipeline_failure(failing.id, RuntimeError('x'), 1, final=True, stage=PipelineStage.TRANSCRIBED))

        self.assertEqual(mail.outbox, [])

    def test_an_account_without_a_verified_email_or_inactive_gets_none(self):
        User.objects.filter(pk=self.ana.pk).update(is_email_verified=False)
        User.objects.filter(pk=self.edu.pk).update(is_active=False)
        project = self.waiting()

        self.committed(lambda: _hand_to_review(project.id))

        self.assertEqual(mail.outbox, [])
        self.assertEqual(set(sent()), {'ana', 'edu', 'vera'})

    def test_a_payment_sends_no_email(self):
        for approved in (True, False):
            payment = Payment.objects.create(
                workspace=self.workspace, created_by=self.ana, tokens=1500, amount_usd=Decimal('15.00'), provider='simulated',
            )
            self.committed(lambda: settle_payment(payment.pk, approved=approved))

        self.assertEqual([kind for kind, _, _ in sent()['ana']], ['payment_approved', 'payment_rejected'])
        self.assertEqual(mail.outbox, [])

    def test_the_examples_of_the_demo_seed_send_no_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.write(self.workspace, {self.ana.pk}, 'project_ready', 'Ejemplo está listo para revisar', 'Encontramos 3 clips.')

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(mail.outbox, [])


class FailingEmailTests(EmailHarness):
    def test_a_send_that_fails_changes_neither_the_event_nor_the_bell(self):
        project = self.ingesting()

        with patch(SEND, side_effect=smtplib.SMTPException('texto que no va al log')) as send, \
                self.assertLogs('apps.notifications.tasks', logging.WARNING) as logs:
            self.fail_project(project)

        self.assertEqual(send.call_count, 2)
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(Transaction.objects.get(project=project).status, Transaction.Status.FAILED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal('100'))
        self.assertEqual(set(sent()), {'ana', 'edu', 'vera'})
        row = Notification.objects.get(user=self.ana)
        self.assertIn(
            f'WARNING:apps.notifications.tasks:notification_email.send_failed kind=project_failed '
            f'notification_id={row.pk} user_id={self.ana.pk} error=SMTPException',
            logs.output,
        )
        self.assertNotIn('texto', ' '.join(logs.output))

    def test_one_failed_send_does_not_stop_the_other_emails(self):
        project = self.waiting()
        real = mail.EmailMultiAlternatives.send

        def first_fails(message, *args, **kwargs):
            if message.to == ['ana@test.com']:
                raise OSError('texto')
            return real(message, *args, **kwargs)

        with patch(SEND, autospec=True, side_effect=first_fails):
            self.assertTrue(self.committed(lambda: _hand_to_review(project.id)))

        self.assert_one_email_each('edu')

    def test_a_queue_that_refuses_the_message_changes_neither_the_event_nor_the_bell(self):
        project = self.waiting()

        with patch(DELAY, side_effect=ConnectionError('texto que no va al log')) as delay, \
                self.assertLogs('apps.notifications.services', logging.ERROR) as logs:
            self.assertTrue(self.committed(lambda: _hand_to_review(project.id)))

        self.assertEqual(delay.call_count, 3)
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(set(sent()), {'ana', 'edu', 'vera'})
        self.assertIn('notifications.email_not_queued kind=project_ready', logs.output[0])
        self.assertIn('error=ConnectionError', logs.output[0])
        self.assertNotIn('texto', ' '.join(logs.output))

    def test_the_task_gets_only_the_id_of_the_notification(self):
        project = self.waiting()

        with patch(DELAY) as delay:
            self.committed(lambda: _hand_to_review(project.id))

        self.assertEqual(
            sorted(call.args for call in delay.call_args_list),
            sorted((str(pk),) for pk in Notification.objects.values_list('pk', flat=True)),
        )
        self.assertTrue(all(not call.kwargs for call in delay.call_args_list))


class TaskTests(EmailHarness):
    def notification(self, user, kind='project_ready'):
        return Notification.objects.create(
            user=user, workspace=self.workspace, kind=kind, title='Podcast 42 está listo para revisar', body='Encontramos 3 clips.',
        )

    def test_a_notification_that_is_gone_sends_nothing(self):
        row = self.notification(self.ana)
        row_id = str(row.pk)
        row.delete()

        send_notification_email(row_id)

        self.assertEqual(mail.outbox, [])

    def test_who_left_the_workspace_gets_nothing(self):
        row = self.notification(self.edu)
        WorkspaceMember.objects.filter(workspace=self.workspace, user=self.edu).delete()

        send_notification_email(str(row.pk))

        self.assertEqual(mail.outbox, [])

    def test_kinds_without_an_email_send_nothing(self):
        for kind in ('payment_approved', 'payment_rejected'):
            send_notification_email(str(self.notification(self.ana, kind).pk))

        self.assertEqual(mail.outbox, [])

    def test_every_kind_with_an_email_has_its_switch(self):
        fields = {field.name for field in NotificationPreference._meta.get_fields()}

        self.assertLessEqual({str(kind) for kind in EMAIL_KINDS}, fields)

    def test_the_log_has_ids_only(self):
        row = self.notification(self.ana)

        with self.assertLogs('apps.notifications.tasks', logging.INFO) as logs:
            send_notification_email(str(row.pk))

        self.assertEqual(logs.output, [
            f'INFO:apps.notifications.tasks:notification_email.sent kind=project_ready notification_id={row.pk} user_id={self.ana.pk}',
        ])


class NoTypedTextTests(EmailHarness):
    """The title of the project, the name of the workspace and the names of people stay out of every email."""

    def setUp(self):
        super().setUp()
        title, workspace_name, first_name, last_name = TYPED
        Workspace.objects.filter(pk=self.workspace.pk).update(name=workspace_name)
        User.objects.update(first_name=first_name, last_name=last_name)
        self.workspace.refresh_from_db()
        self.title = title

    def project(self, clips=(), **fields):
        project = super().project(clips, **fields)
        VideoProject.objects.filter(pk=project.pk).update(title=self.title)
        project.refresh_from_db()
        return project

    def assert_no_typed_text(self, expected):
        self.assertEqual(len(mail.outbox), expected)
        for message in mail.outbox:
            content = whole(message)
            for typed in ('Título', 'secreto', 'Estudio', 'Norte', 'Anabella', 'Quiroga', '<b>', '<i>', 'ana@test.com', 'Podcast'):
                self.assertNotIn(typed, content)

    def test_no_event_email_carries_typed_text(self):
        ready, done, failing = self.waiting(), self.rendering(DONE), self.ingesting()

        self.committed(lambda: _hand_to_review(ready.id))
        self.committed(lambda: finalize_project_render(done.id))
        self.fail_project(failing)

        # The bell does carry the title: that is where the difference is.
        self.assertIn(f'{self.title} está listo para revisar', [title for _, title, _ in sent()['ana']])
        self.assert_no_typed_text(6)

    def test_the_email_of_one_clip_that_could_not_be_rendered_carries_no_typed_text(self):
        # The reservation is written with the clip's title; the notice says what the activity says, without it.
        project = self.project(
            clips=[FAILED], status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )
        clip = VideoClip.objects.get(project=project)
        VideoClip.objects.filter(pk=clip.pk).update(title='Título secreto', s3_object_key='old-key')
        txn = reserve_funds(self.wallet.id, Decimal('9.00'), project=project, description='Render de clip: Título secreto')
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            'clip_render_reservation_id': str(txn.id), 'clip_render_clip_ids': [str(clip.pk)], 'clip_render_seconds': 5.0,
            'clip_render_scope': 'clip', 'clip_render_previous': {str(clip.pk): {'status': 'completed', 'key': 'old-key'}},
        })

        self.committed(lambda: finalize_project_render(project.id))

        text = 'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 9 tokens.'
        self.assertEqual({body for _, _, body in sent()['ana']}, {text})
        self.assert_no_typed_text(2)
        # Not the email of a render that ended: its own subject, opening and closing.
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'No pudimos renderizar un clip de tu proyecto')
        opening = f'No pudimos renderizar un clip de un proyecto de tu espacio de trabajo en OneCreator. {text}'
        closing = 'Abrí el proyecto para ver el clip y volver a intentarlo cuando quieras.'
        for content in (message.body, message.alternatives[0][0]):
            self.assertIn(opening, content)
            self.assertIn(closing, content)
            self.assertNotIn('Terminó el render', content)
            self.assertNotIn('lo que quedó listo', content)
        self.assertIn('tenés activado el aviso «El render terminó»', message.body)
        self.assertEqual(
            set(Notification.objects.values_list('kind', 'reason')), {('render_finished', Notification.CLIP_RENDER_FAILED)},
        )
        self.assertIn(text, mail.outbox[0].body)
        self.assertIn(f'https://app.onecreator.test/proyectos/{project.id}/clips?espacio={self.workspace.id}\n', mail.outbox[0].body)

    def test_the_low_balance_email_carries_no_typed_text(self):
        row = Notification.objects.create(
            user=self.ana, workspace=self.workspace, kind='low_balance', title='Te quedan 300 tokens',
            body='El saldo quedó por debajo de lo que cuesta un video promedio.',
        )

        send_notification_email(str(row.pk))

        self.assert_no_typed_text(1)
        self.assertIn('Te quedan 300 tokens', mail.outbox[0].body)
        self.assertIn(f'https://app.onecreator.test/uso/comprar?espacio={self.workspace.id}\n', mail.outbox[0].body)

    def test_what_goes_into_the_html_is_escaped(self):
        row = Notification(
            user=self.ana, workspace=self.workspace, kind='project_failed', title='x', body='<script>alert(1)</script> & más',
        )

        _, text, html = build(row)

        self.assertIn('<script>alert(1)</script> & más', text)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; &amp; más', html)


class QueuedAfterTheBellTests(TransactionTestCase):
    """
    With a real worker the task runs in another process: it must be queued
    only once the notification's row is committed, or it finds no row and
    sends nothing. (In these settings the task runs inline, so only the
    moment it is queued shows the difference.)
    """

    def test_the_task_is_queued_once_another_connection_can_read_the_notification(self):
        ana = make_user('ana')
        workspace = team(ana)
        seen = []

        def from_another_connection(notification_id):
            def read():
                try:
                    seen.append(Notification.objects.filter(pk=notification_id).exists())
                finally:
                    connection.close()

            reader = threading.Thread(target=read)
            reader.start()
            reader.join(10)
            seen.append(connection.in_atomic_block)

        with patch(DELAY, side_effect=from_another_connection) as delay:
            services._write(workspace, {ana.pk}, 'project_ready', 'Podcast 42 está listo para revisar', 'Encontramos 3 clips.')

        self.assertEqual(delay.call_count, 1)
        # The row is there for the worker, and the write's transaction is over.
        self.assertEqual(seen, [True, False])
