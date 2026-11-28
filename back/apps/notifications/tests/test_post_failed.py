"""
«Una publicación falló» (``post_failed``, map 1.11 and gap 16): every write
that moves a scheduled post to FAILED tells the workspace's people once, in
the bell and by email, after the commit and without ever breaking the step.

- the publisher (``upload_to_social_network``): a refusal, an unknown outcome,
  no account, running out of retries, the lock that never came;
- the AI text of a post that was going out with no text
  (``_fail_seo_post``, ``fail_lost_ai_texts``);
- a network that is disconnected (``fail_network_posts``).
"""
import logging
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.integrations.social import OutcomeUnknownError, PermanentError, RetryableError
from apps.integrations.tests.helpers import PUBLISH, PUBLISHED, give_social_profile, take_social_profile
from apps.notifications import services
from apps.notifications.models import Notification
from apps.users.models import NotificationPreference, User
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.serializers_social import ScheduledPostSerializer
from apps.videos.services import scheduled_posts
from apps.videos.tasks import upload_to_social_network
from apps.videos.tasks.social import _fail_post, _fail_seo_post
from apps.videos.tests.fakes import CapturedTaskFailures

from .helpers import BellIsBroken, broken_bell, sent
from .test_emails import whole
from .test_events import EventHarness

Status, Platform = ScheduledPost.Status, ScheduledPost.Platform
PRESIGN = 'apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url'
REJECTED = (
    'post_failed', 'La remontada: no se pudo publicar en Instagram Reels',
    'Instagram Reels rechazó la publicación. Revisá el video y el texto, y reintentá.',
)
NO_TEXT = ('post_failed', 'La remontada: no se pudo publicar en Instagram Reels', 'No pudimos generar el texto. Escribilo vos.')


@override_settings(FRONTEND_URL='https://app.onecreator.test/')
class PostHarness(CapturedTaskFailures, EventHarness):
    """The team of EventHarness with verified emails, an account at the provider and a rendered clip."""

    def setUp(self):
        super().setUp()
        User.objects.update(is_email_verified=True)
        give_social_profile(self.workspace, 'PROFILE-KEY')
        self.video = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.edu, title='Entrevista', metadata={'use_seo': True},
        )
        self.clip = VideoClip.objects.create(
            project=self.video, title='La remontada', start_time=0, end_time=30,
            status=VideoClip.Status.COMPLETED, s3_object_key='clips/a.mp4',
        )
        presign = patch(PRESIGN, return_value='https://r2.test/clips/a.mp4?sig=1')
        presign.start()
        self.addCleanup(presign.stop)

    def make_post(self, platform=Platform.INSTAGRAM_REELS, status=Status.QUEUED, clip=None, **fields):
        # seo_decided: the signal asks for no AI text (the tests that want one say so).
        post = ScheduledPost(
            video_clip=clip or self.clip, platform=platform, status=status, publish_at='2030-01-01T00:00:00Z', **fields,
        )
        post.seo_decided = True
        post.save()
        return post

    def publish(self, post, **provider):
        """The worker's run for ``post`` and what it left for after the commit."""
        with patch(PUBLISH, **provider) as publish:
            result = self.committed(lambda: upload_to_social_network.apply(args=[str(post.pk)]))
        post.refresh_from_db()
        return result, publish

    def assert_told_once_about(self, post, expected):
        self.assert_everyone_got(expected)
        self.assertEqual(set(Notification.objects.values_list('post_id', 'project_id')), {(post.pk, self.video.pk)})


class PublisherTests(PostHarness):
    def test_a_refusal_tells_every_member_with_the_words_of_publicaciones(self):
        post = self.make_post()

        result, _ = self.publish(post, side_effect=PermanentError('provider_rejected', 'ayrshare http=400 code=1'))

        self.assertTrue(result.failed())
        self.assertEqual(post.status, Status.FAILED)
        self.assert_told_once_about(post, REJECTED)
        self.assertEqual(REJECTED[2], ScheduledPostSerializer(post).data['error_message'])
        self.assertEqual(set(Notification.objects.values_list('reason', flat=True)), {'provider_rejected'})

    def test_a_clip_without_a_title_is_named_by_its_project(self):
        VideoClip.objects.filter(pk=self.clip.pk).update(title='')

        self.publish(self.make_post(), side_effect=PermanentError('provider_rejected'))

        self.assertEqual(sent()['ana'][0][1], 'Entrevista: no se pudo publicar en Instagram Reels')

    def test_an_unknown_outcome_never_reads_as_a_failure(self):
        post = self.make_post(Platform.YOUTUBE_SHORTS)

        self.publish(post, side_effect=OutcomeUnknownError(detail='ayrshare http=504'))

        self.assert_told_once_about(post, (
            'post_failed', 'La remontada: no sabemos si se publicó en YouTube Shorts',
            'No sabemos si se publicó: YouTube Shorts no respondió a tiempo. Revisá la cuenta antes de reintentar.',
        ))

    def test_without_the_account_it_is_told_and_nothing_is_sent(self):
        take_social_profile(self.workspace)
        post = self.make_post()

        result, publish = self.publish(post)

        publish.assert_not_called()
        self.assertTrue(result.failed())
        self.assert_told_once_about(post, (
            'post_failed', 'La remontada: no se pudo publicar en Instagram Reels',
            'La cuenta de Instagram Reels no está conectada. Conectala y reintentá.',
        ))

    def test_running_out_of_retries_tells_once_not_once_per_attempt(self):
        post = self.make_post()

        _, publish = self.publish(post, side_effect=RetryableError(detail='ayrshare http=503'))

        self.assertEqual(publish.call_count, 4)
        self.assertEqual(post.status, Status.FAILED)
        self.assert_told_once_about(post, (
            'post_failed', 'La remontada: no se pudo publicar en Instagram Reels',
            'Instagram Reels no respondió después de varios intentos. Probá de nuevo en unos minutos.',
        ))

    def test_a_lock_that_never_comes_tells(self):
        post = self.make_post()

        with patch('apps.videos.tasks.social.caches') as caches:
            caches.__getitem__.return_value.add.side_effect = ConnectionError('redis')
            result = self.committed(lambda: upload_to_social_network.apply(args=[str(post.pk)]))

        post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(post.status, Status.FAILED)
        self.assert_told_once_about(post, (
            'post_failed', 'La remontada: no se pudo publicar en Instagram Reels',
            'No pudimos publicar el post. Probá de nuevo en unos minutos.',
        ))

    def test_an_unexpected_error_tells_too(self):
        post = self.make_post()

        self.publish(post, side_effect=KeyError('postIds'))

        self.assertEqual(sent()['ana'][0][1], 'La remontada: no sabemos si se publicó en Instagram Reels')
        self.assertEqual(Notification.objects.count(), 3)

    def test_a_post_that_goes_out_tells_nobody(self):
        post = self.make_post()

        self.publish(post, return_value=PUBLISHED)

        self.assertEqual(post.status, Status.PUBLISHED)
        self.assertEqual(sent(), {})

    def test_a_late_message_for_a_failed_post_does_not_tell_again(self):
        post = self.make_post()
        self.publish(post, side_effect=PermanentError('provider_rejected'))

        result, _ = self.publish(post)

        self.assertEqual(result.result, 'Skipped')
        self.assert_told_once_about(post, REJECTED)

    def test_failed_written_over_failed_does_not_tell_again(self):
        post = self.make_post()
        self.publish(post, side_effect=PermanentError('provider_rejected'))

        self.committed(lambda: _fail_post(post.pk, error_log='outcome_unknown (critical KeyError)'))

        post.refresh_from_db()
        self.assertEqual(post.error_log, 'outcome_unknown (critical KeyError)')
        self.assert_told_once_about(post, REJECTED)

    def test_a_post_retried_that_fails_again_tells_again(self):
        post = self.make_post()
        self.publish(post, side_effect=PermanentError('provider_rejected'))

        with patch('apps.videos.services.scheduled_posts._queue_dispatch'):
            self.committed(lambda: scheduled_posts.retry_post(self.workspace, post.pk))
        ScheduledPost.objects.filter(pk=post.pk, status=Status.SCHEDULED).update(status=Status.QUEUED)
        self.publish(post, side_effect=PermanentError('account_expired'))

        self.assertEqual(
            [body for _, _, body in sent()['ana']],
            [REJECTED[2], 'La cuenta de Instagram Reels venció. Reconectala y reintentá.'],
        )
        self.assertEqual(Notification.objects.count(), 6)

    def test_rolled_back_it_tells_nobody(self):
        post = self.make_post()

        self.rolled_back(lambda: _fail_post(post.pk, error_log='provider_rejected'))

        post.refresh_from_db()
        self.assertEqual(post.status, Status.QUEUED)
        self.assertEqual(sent(), {})

    def test_a_retry_that_was_faster_than_the_commit_tells_nobody(self):
        post = self.make_post(status=Status.SCHEDULED)

        self.committed(lambda: services.notify_post_failed(post.pk))

        self.assertEqual(sent(), {})

    def test_a_broken_bell_does_not_change_what_the_worker_did(self):
        post = self.make_post()

        with broken_bell() as create, self.assertLogs('apps.notifications.services', logging.ERROR) as logs:
            result, _ = self.publish(post, side_effect=PermanentError('provider_rejected', 'ayrshare http=400'))

        create.assert_called()
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, PermanentError)
        self.assertEqual((post.status, post.error_log), (Status.FAILED, 'provider_rejected (ayrshare http=400)'))
        self.assertIn(f'post_id={post.pk}', logs.output[0])
        self.assertIn(BellIsBroken.__name__, logs.output[0])
        self.assertNotIn('texto', ' '.join(logs.output))


class AiTextTests(PostHarness):
    def test_a_post_that_was_going_out_with_no_text_tells(self):
        post = self.make_post(status=Status.SCHEDULED)

        self.assertTrue(self.committed(lambda: _fail_seo_post(post.pk, 'seo_failed:ValueError', redispatch=False)))

        post.refresh_from_db()
        self.assertEqual(post.status, Status.FAILED)
        self.assert_told_once_about(post, NO_TEXT)

    def test_a_second_failure_of_the_same_text_does_not_tell_again(self):
        post = self.make_post(status=Status.SCHEDULED)
        self.committed(lambda: _fail_seo_post(post.pk, 'seo_dispatch_failed', redispatch=False))

        self.assertTrue(self.committed(lambda: _fail_seo_post(post.pk, 'seo_request_expired', redispatch=False)))

        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log), (Status.FAILED, 'seo_request_expired'))
        self.assert_told_once_about(post, NO_TEXT)

    def test_a_post_that_keeps_its_status_tells_nobody(self):
        draft = self.make_post(status=Status.DRAFT)
        with_text = self.make_post(Platform.TIKTOK, status=Status.SCHEDULED, caption='Mío')
        with_tags = self.make_post(Platform.YOUTUBE_SHORTS, status=Status.SCHEDULED, hashtags=['gol'])
        other = VideoClip.objects.create(project=self.video, title='Otro', start_time=0, end_time=5)
        earlier = self.make_post(clip=other, status=Status.SCHEDULED, generated_caption='IA', generated_hashtags=['ia'])
        out = self.make_post(Platform.TIKTOK, clip=other, status=Status.PUBLISHED)

        for post in (draft, with_text, with_tags, earlier, out):
            self.committed(lambda: _fail_seo_post(post.pk, 'capacity_timeout', redispatch=False))

        self.assertEqual(
            sorted(ScheduledPost.objects.values_list('status', flat=True)),
            sorted([Status.DRAFT, Status.SCHEDULED, Status.SCHEDULED, Status.SCHEDULED, Status.PUBLISHED]),
        )
        self.assertEqual(sent(), {})

    def test_a_failure_to_publish_is_not_told_again_by_its_text(self):
        post = self.make_post()
        self.publish(post, side_effect=PermanentError('provider_rejected'))

        self.assertTrue(self.committed(lambda: _fail_seo_post(post.pk, 'capacity_timeout', redispatch=False)))

        self.assert_told_once_about(post, REJECTED)

    def test_a_lost_ai_text_tells(self):
        now = timezone.now()
        post = self.make_post(status=Status.SCHEDULED)
        ScheduledPost.objects.filter(pk=post.pk).update(seo_requested_at=now - timedelta(days=1))

        self.assertEqual(self.committed(lambda: scheduled_posts.fail_lost_ai_texts(now)), 1)

        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log), (Status.FAILED, 'seo_request_expired'))
        self.assert_told_once_about(post, NO_TEXT)

    def test_a_broker_that_refuses_the_text_of_a_scheduled_post_tells(self):
        from apps.videos.signals import queue_post_seo

        post = self.make_post(status=Status.SCHEDULED)

        with patch('apps.videos.tasks.process_video_seo.delay', side_effect=ConnectionError('broker')):
            self.committed(lambda: queue_post_seo(post.pk))

        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log), (Status.FAILED, 'seo_dispatch_failed'))
        self.assert_told_once_about(post, NO_TEXT)


class DisconnectedNetworkTests(PostHarness):
    def test_one_post_is_told_like_any_other_failure(self):
        post = self.make_post(Platform.TIKTOK, status=Status.SCHEDULED)
        self.make_post(Platform.YOUTUBE_SHORTS, status=Status.SCHEDULED)

        self.assertEqual(self.committed(lambda: scheduled_posts.fail_network_posts(self.workspace, Platform.TIKTOK)), 1)

        self.assert_told_once_about(post, (
            'post_failed', 'La remontada: no se pudo publicar en TikTok',
            'La cuenta de TikTok no está conectada. Conectala y reintentá.',
        ))

    def test_several_posts_are_told_once_for_all(self):
        other = VideoClip.objects.create(project=self.video, title='Otro', start_time=0, end_time=5)
        third = VideoClip.objects.create(project=self.video, title='Tercero', start_time=0, end_time=5)
        self.make_post(Platform.TIKTOK, status=Status.SCHEDULED)
        self.make_post(Platform.TIKTOK, status=Status.QUEUED, clip=other)
        self.make_post(Platform.TIKTOK, status=Status.DRAFT, clip=third)

        self.assertEqual(self.committed(lambda: scheduled_posts.fail_network_posts(self.workspace, Platform.TIKTOK)), 2)

        self.assert_everyone_got((
            'post_failed', 'Se desconectó TikTok: 2 publicaciones no van a salir',
            'Reconectá la cuenta de TikTok y reintentalas desde Publicaciones.',
        ))
        self.assertEqual(set(Notification.objects.values_list('post_id', 'project_id')), {(None, None)})
        self.assertEqual([message.subject for message in mail.outbox], ['Hay publicaciones que no van a salir'] * 3)
        self.assertIn(f'https://app.onecreator.test/publicaciones?espacio={self.workspace.id}\n', mail.outbox[0].body)

    def test_nothing_to_fail_tells_nobody(self):
        self.make_post(Platform.TIKTOK, status=Status.DRAFT)

        self.assertEqual(self.committed(lambda: scheduled_posts.fail_network_posts(self.workspace, Platform.TIKTOK)), 0)

        self.assertEqual(sent(), {})

    def test_a_broken_bell_does_not_stop_the_disconnection(self):
        post = self.make_post(Platform.TIKTOK, status=Status.SCHEDULED)

        failed = self.assert_survives_a_broken_bell(lambda: scheduled_posts.fail_network_posts(self.workspace, Platform.TIKTOK))

        post.refresh_from_db()
        self.assertEqual((failed, post.status), (1, Status.FAILED))


class PostFailedEmailTests(PostHarness):
    def assert_emails_to(self, *usernames):
        self.assertEqual(sorted(address for message in mail.outbox for address in message.to),
                         sorted(f'{name}@test.com' for name in usernames))
        return mail.outbox[0]

    def test_the_email_says_what_happened_and_leads_to_the_post(self):
        post = self.make_post()

        self.publish(post, side_effect=PermanentError('provider_rejected', 'ayrshare http=400'))

        message = self.assert_emails_to('ana', 'edu', 'vera')
        self.assertEqual(message.subject, 'Una publicación no salió')
        self.assertIn(REJECTED[2], message.body)
        link = f'https://app.onecreator.test/publicaciones?publicacion={post.pk}&espacio={self.workspace.id}'
        self.assertIn(f'Ver la publicación:\n{link}\n', message.body)
        self.assertIn(f'href="{link.replace("&", "&amp;")}"', message.alternatives[0][0])
        self.assertIn('tenés activado el aviso «Una publicación falló»', message.body)

    def test_an_unknown_outcome_has_its_own_subject(self):
        self.publish(self.make_post(), side_effect=OutcomeUnknownError(detail='ayrshare http=504'))

        message = self.assert_emails_to('ana', 'edu', 'vera')
        self.assertEqual(message.subject, 'No sabemos si una publicación salió')
        self.assertIn('Puede que ya esté publicada', message.body)
        self.assertNotIn('No pudimos publicar', message.body)

    def test_who_turned_it_off_gets_the_bell_and_no_email(self):
        NotificationPreference.objects.create(user=self.vera, post_failed=False)
        NotificationPreference.objects.create(user=self.edu, project_failed=False)

        self.publish(self.make_post(), side_effect=PermanentError('provider_rejected'))

        self.assert_emails_to('ana', 'edu')
        self.assertEqual(sent()['vera'], [REJECTED])

    def test_no_email_carries_typed_text(self):
        VideoProject.objects.filter(pk=self.video.pk).update(title='<b>Título & secreto</b>')
        VideoClip.objects.filter(pk=self.clip.pk).update(title='Gol <i>de</i> Quiroga')
        other = VideoClip.objects.create(project=self.video, title='Anabella', start_time=0, end_time=5)

        self.publish(self.make_post(caption='Texto mío', hashtags=['propio']), side_effect=PermanentError('provider_rejected', 'x'))
        self.publish(self.make_post(Platform.TIKTOK), side_effect=OutcomeUnknownError(detail='x'))
        self.committed(lambda: _fail_seo_post(self.make_post(clip=other, status=Status.SCHEDULED).pk, 'seo_failed:X', redispatch=False))
        for clip in (self.clip, other):
            self.make_post(Platform.YOUTUBE_SHORTS, clip=clip, status=Status.SCHEDULED)
        self.committed(lambda: scheduled_posts.fail_network_posts(self.workspace, Platform.YOUTUBE_SHORTS))

        # The bell does name the clip: that is where the difference is.
        self.assertIn('Gol <i>de</i> Quiroga: no se pudo publicar en Instagram Reels', [title for _, title, _ in sent()['ana']])
        self.assertEqual(len(mail.outbox), 12)
        for message in mail.outbox:
            content = whole(message)
            for typed in ('Título', 'secreto', 'Gol', 'Quiroga', 'Anabella', 'Entrevista', 'Equipo', 'Texto mío', 'propio',
                          '<b>', '<i>', 'ana@test.com'):
                self.assertNotIn(typed, content)
