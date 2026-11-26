"""
Reads and downloads of a project (views_project_data.py):

- GET  /projects/<id>/activity/: built from what is stored (creation,
  reservations and their settlement, AI usage, approval, posts), newest first.
- POST /projects/<id>/clips/download/: a .zip of the ready clips, streamed
  from storage, capped in size, count and time, temporary files deleted.
- GET  /clips/<id>/download/: a signed URL that saves the clip as a file.

Every one is scoped to the active workspace (404 elsewhere) and open to every
role, Lector included.
"""
import io
import os
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlsplit

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.payments.models import Transaction, Wallet
from apps.payments.services import wallet_service
from apps.videos.models import AIUsageRecord, ScheduledPost, VideoClip, VideoProject
from apps.videos.services import clip_download
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.test_active_workspace import VIEWER, fund, header, home_of, join, make_user, team

ACTIVITY_TYPES = {
    'project_created', 'prompt_applied', 'review_ready', 'review_approved', 'render_finished', 'clip_edited',
    'clip_retried', 'post_scheduled', 'post_published', 'post_failed', 'project_renamed', 'project_failed',
    'tokens_reserved', 'tokens_charged', 'tokens_refunded',
}


def activity_url(project):
    return reverse('project-activity', args=[project.pk])


def zip_url(project):
    return reverse('project-clips-download', args=[project.pk])


def clip_url(clip):
    return reverse('clip-download', args=[clip.pk])


def at(minutes):
    return timezone.now() - timedelta(days=1) + timedelta(minutes=minutes)


def reserve(workspace, amount, user, minutes):
    wallet = Wallet.objects.get(workspace=workspace)
    txn = wallet_service.reserve_funds(wallet_id=wallet.id, amount=Decimal(amount), user=user, description="x")
    Transaction.objects.filter(pk=txn.pk).update(created_at=at(minutes))
    return txn


def settle(txn, minutes):
    Transaction.objects.filter(pk=txn.pk).update(settled_at=at(minutes))


def usage(project, stage, minutes, success=True):
    record = AIUsageRecord.objects.create(
        project=project, stage=stage, pipeline_version='v2', attempt=1, role='primary',
        provider='p', model='m', success=success, latency_seconds=1.0,
    )
    AIUsageRecord.objects.filter(pk=record.pk).update(created_at=at(minutes))


def write_media(relative, data):
    path = os.path.join(str(settings.MEDIA_ROOT), relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(data)
    return path


class ProjectDataHarness(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        fund(self.home, '500.00')
        self.boss = make_user('boss')
        self.team = team(self.boss)
        fund(self.team)
        self.client.force_authenticate(self.alice)

    def project(self, workspace=None, user=None, **fields):
        fields.setdefault('title', 'Entrevista con Ana')
        project = VideoProject.objects.create(
            workspace=workspace or self.home, uploaded_by=user or self.alice, source_file='test/file.mp4', **fields,
        )
        VideoProject.objects.filter(pk=project.pk).update(created_at=at(0))
        project.refresh_from_db()
        return project

    def clip(self, project, title='Clip', status=VideoClip.Status.COMPLETED, data=b'video-bytes', minutes=1):
        key = write_media(f'videos/{project.pk}/{title}-{minutes}.mp4', data) if data is not None else None
        clip = VideoClip.objects.create(
            project=project, title=title, start_time=0, end_time=5, status=status, s3_object_key=key,
        )
        VideoClip.objects.filter(pk=clip.pk).update(created_at=at(minutes))
        clip.refresh_from_db()
        return clip


# --- activity -----------------------------------------------------------------

class ProjectActivityTests(ProjectDataHarness):
    def test_a_rendered_project_tells_its_story_newest_first(self):
        project = self.project(
            status=VideoProject.Status.PARTIAL, pipeline_stage=PipelineStage.PARTIAL,
            ai_rationale_log={'suggestions_count': 3},
        )
        creation = reserve(self.home, '20', self.alice, 1)
        wallet_service.commit_reservation(creation.pk)
        settle(creation, 30)
        usage(project, AIUsageRecord.Stage.PROMPT_PARSE, 2)
        usage(project, AIUsageRecord.Stage.SELECTION, 8)
        usage(project, AIUsageRecord.Stage.SELECTION, 5, success=False)
        approval = reserve(self.home, '12', self.alice, 15)
        wallet_service.commit_partial_reservation(approval.pk, Decimal('8'))
        settle(approval, 26)
        clips = [self.clip(project, f'Clip {n}', minutes=10) for n in range(2)]
        self.clip(project, 'Roto', status=VideoClip.Status.FAILED, data=None, minutes=10)
        post = ScheduledPost.objects.create(
            video_clip=clips[0], platform='TIKTOK', publish_at=at(40), status=ScheduledPost.Status.PUBLISHED,
        )
        ScheduledPost.objects.filter(pk=post.pk).update(created_at=at(28), updated_at=at(41))
        ScheduledPost.objects.create(video_clip=clips[1], platform='YOUTUBE_SHORTS', publish_at=at(50))  # DRAFT
        project.metadata = {
            'reservation_id': str(creation.pk), 'render_reservation_id': str(approval.pk),
            'render_clip_ids': [str(clip.pk) for clip in clips] + ['x'],
            'pte': {'status': 'applied', 'applied': {}, 'ignored': []},
        }
        project.save()

        response = self.client.get(activity_url(project))

        self.assertEqual(response.status_code, 200)
        items = response.json()
        story = [(item['type'], item['description'], item['amount']) for item in items]
        self.assertEqual(story, [
            ('post_published', 'Se publicó «Clip 0» en TikTok', None),
            ('tokens_charged', 'Cobro del procesamiento', '-20.0000000000'),
            ('post_scheduled', 'Se programó «Clip 0» en TikTok', None),
            ('tokens_refunded', 'Devolución por los clips que no se generaron', '4.0000000000'),
            ('tokens_charged', 'Cobro del render', '-8.0000000000'),
            ('render_finished', 'Terminó el render: 2 clips listos y 1 con error', None),
            ('tokens_reserved', 'Reserva por el render', '-12.0000000000'),
            ('review_approved', 'Aprobó 3 clips para renderizar', None),
            ('review_ready', 'Listo para revisar: 3 clips sugeridos', None),
            ('prompt_applied', 'La IA aplicó la configuración del prompt', None),
            ('tokens_reserved', 'Reserva por el procesamiento', '-20.0000000000'),
            ('project_created', 'Creó el proyecto', None),
        ])
        by_type = {item['type']: item for item in items}
        self.assertEqual(by_type['project_created']['actor'], {'id': str(self.alice.pk), 'name': 'alice'})
        self.assertEqual(by_type['review_approved']['actor']['id'], str(self.alice.pk))
        self.assertIsNone(by_type['render_finished']['actor'])
        self.assertEqual(by_type['post_published']['platform'], 'TIKTOK')
        self.assertEqual(len({item['id'] for item in items}), len(items))
        self.assertTrue({item['type'] for item in items} <= ACTIVITY_TYPES)
        # Stable ids: the same feed twice has the same ids.
        self.assertEqual([item['id'] for item in self.client.get(activity_url(project)).json()], [item['id'] for item in items])

    def test_a_project_just_created_has_only_its_creation_and_reservation(self):
        project = self.project()
        txn = reserve(self.home, '10', self.alice, 0)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([item['type'] for item in items], ['tokens_reserved', 'project_created'])

    def test_a_project_without_anything_recorded_answers_its_creation(self):
        project = self.project()
        self.assertEqual([item['type'] for item in self.client.get(activity_url(project)).json()], ['project_created'])

    def test_a_failed_project_shows_the_failure_and_the_refund(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
                               pipeline_error_code='AINoSpeechDetectedError', pipeline_error_at=at(6))
        txn = reserve(self.home, '10', self.alice, 1)
        wallet_service.rollback_reservation(txn.pk)
        settle(txn, 7)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual(
            [(item['type'], item['amount']) for item in items],
            [('tokens_refunded', '10.0000000000'), ('project_failed', None),
             ('tokens_reserved', '-10.0000000000'), ('project_created', None)],
        )

    def test_a_reservation_settled_before_the_new_columns_uses_what_there_is(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_error_at=at(6))
        txn = reserve(self.home, '10', self.alice, 1)
        wallet_service.rollback_reservation(txn.pk)
        Transaction.objects.filter(pk=txn.pk).update(settled_at=None, reserved_amount=None)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        refund = next(item for item in items if item['type'] == 'tokens_refunded')
        self.assertEqual(refund['amount'], '10.0000000000')

    def test_a_reservation_of_another_workspace_is_never_shown(self):
        project = self.project()
        foreign = reserve(self.team, '10', self.boss, 1)
        project.metadata = {'reservation_id': str(foreign.pk), 'render_reservation_id': 'not-a-uuid'}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([item['type'] for item in items], ['project_created'])

    def test_a_project_of_another_workspace_is_a_404(self):
        foreign = self.project(workspace=self.team, user=self.boss)

        self.assertEqual(self.client.get(activity_url(foreign)).status_code, 404)
        self.assertEqual(self.client.get(activity_url(foreign), **header(self.team)).status_code, 404)

    def test_a_lector_reads_the_activity(self):
        join(self.team, self.alice, VIEWER)
        project = self.project(workspace=self.team, user=self.boss)

        response = self.client.get(activity_url(project), **header(self.team))

        self.assertEqual(response.status_code, 200)

    def test_the_queries_do_not_grow_with_the_posts(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        clip = self.clip(project)
        ScheduledPost.objects.create(video_clip=clip, platform='TIKTOK', publish_at=at(40), status='SCHEDULED')
        with CaptureQueriesContext(connection) as one:
            self.client.get(activity_url(project))
        for _ in range(5):
            extra = self.clip(project, 'Otro')
            ScheduledPost.objects.create(video_clip=extra, platform='TIKTOK', publish_at=at(40), status='FAILED')
        with CaptureQueriesContext(connection) as six:
            items = self.client.get(activity_url(project)).json()

        self.assertEqual(len(one.captured_queries), len(six.captured_queries))
        self.assertEqual(sum(item['type'] == 'post_failed' for item in items), 5)


# --- one clip -----------------------------------------------------------------

class FakeBody:
    def __init__(self, data, fail_after=None):
        self.data, self.fail_after, self.chunk_sizes, self.closed = data, fail_after, [], False

    def iter_chunks(self, size):
        for start in range(0, len(self.data), size):
            if self.fail_after is not None and start >= self.fail_after:
                raise ConnectionResetError("SECRET_LOG_X")
            self.chunk_sizes.append(size)
            yield self.data[start:start + size]

    def close(self):
        self.closed = True


class FakeR2:
    def __init__(self, objects=None, fail=None, fail_after=None):
        self.objects = dict(objects or {})
        self.fail, self.fail_after = fail, fail_after
        self.uploads, self.presigned, self.bodies, self.deleted = {}, [], [], []
        self.listed = []

    def _maybe_fail(self, operation):
        if self.fail == operation:
            raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'SECRET_LOG_X'}}, operation)

    def head_object(self, Bucket, Key):
        self._maybe_fail('HeadObject')
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return {'ContentLength': len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        self._maybe_fail('GetObject')
        body = FakeBody(self.objects[Key], self.fail_after)
        self.bodies.append(body)
        return {'Body': body}

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self._maybe_fail('PutObject')
        with open(path, 'rb') as handle:
            self.uploads[key] = (handle.read(), ExtraArgs)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self._maybe_fail('Presign')
        self.presigned.append((Params, ExpiresIn))
        return f"https://r2.example.test/{Params['Key']}?X-Amz-Signature=x"

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {'Contents': self.listed}

    def delete_objects(self, Bucket, Delete):
        self.deleted.extend(item['Key'] for item in Delete['Objects'])

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


R2 = override_settings(USE_R2=True, CLOUDFLARE_R2_BUCKET_NAME='bucket')


class ClipDownloadTests(ProjectDataHarness):
    def test_a_rendered_clip_gets_an_absolute_url_and_its_filename(self):
        project = self.project()
        clip = self.clip(project, 'Ñandú: ¡el mejor momento!')

        response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['filename'], 'ñandú-el-mejor-momento.mp4')
        self.assertTrue(body['url'].startswith('http://testserver/media/videos/'))
        self.assertTrue(urlsplit(body['url']).path.endswith('.mp4'))
        self.assertIn('expires_at', body)

    @R2
    def test_in_r2_the_url_is_signed_and_asks_to_save_the_file(self):
        project = self.project()
        clip = VideoClip.objects.create(project=project, title='Mi Clip', start_time=0, end_time=1,
                                        status='published', s3_object_key='videos/u/p/c.mp4')
        fake = FakeR2()
        with patch.object(clip_download, '_client', return_value=fake):
            response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['url'], 'https://r2.example.test/videos/u/p/c.mp4?X-Amz-Signature=x')
        params, ttl = fake.presigned[0]
        self.assertEqual(params['ResponseContentDisposition'], 'attachment; filename="mi-clip.mp4"')
        self.assertEqual(params['ResponseContentType'], 'video/mp4')
        self.assertEqual(ttl, 3600)

    def test_a_clip_not_rendered_yet_is_a_409(self):
        project = self.project()
        for status, data in (('draft', None), ('rendering', None), ('failed', None), ('completed', None)):
            with self.subTest(status=status):
                clip = self.clip(project, status=status, data=data)
                response = self.client.get(clip_url(clip))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error'], 'El clip todavía no está renderizado.')

    def test_a_clip_of_another_workspace_is_a_404_and_a_lector_downloads(self):
        foreign = self.clip(self.project(workspace=self.team, user=self.boss))
        self.assertEqual(self.client.get(clip_url(foreign)).status_code, 404)
        self.assertEqual(self.client.get(clip_url(foreign), **header(self.team)).status_code, 404)

        join(self.team, self.alice, VIEWER)
        self.assertEqual(self.client.get(clip_url(foreign), **header(self.team)).status_code, 200)

    def test_a_missing_file_or_a_key_outside_media_is_a_storage_error(self):
        project = self.project()
        missing = VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                           s3_object_key=os.path.join(str(settings.MEDIA_ROOT), 'nope.mp4'))
        outside = VideoClip.objects.create(project=project, title='b', start_time=0, end_time=1, status='completed',
                                           s3_object_key='/etc/passwd')
        for clip in (missing, outside):
            with self.subTest(key=clip.s3_object_key):
                response = self.client.get(clip_url(clip))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['code'], 'storage_unavailable')

    @R2
    def test_a_storage_error_while_signing_is_a_503(self):
        project = self.project()
        clip = VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                        s3_object_key='videos/a.mp4')
        with patch.object(clip_download, '_client', return_value=FakeR2(fail='Presign')), \
                self.assertLogs('apps.videos.views_project_data', 'ERROR') as logs:
            response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET_LOG_X', '\n'.join(logs.output))


# --- .zip of the project --------------------------------------------------------

class ProjectZipTests(ProjectDataHarness):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        patcher = override_settings(CLIPS_ZIP_TMP_DIR=self.tmp)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def assert_no_temp_files(self):
        self.assertEqual(os.listdir(self.tmp), [])

    def stored_zips(self):
        root = os.path.join(str(settings.MEDIA_ROOT), 'exports', 'zips')
        return [os.path.join(folder, name) for folder, _dirs, names in os.walk(root) for name in names]

    def test_the_ready_clips_go_in_one_zip_with_readable_names(self):
        project = self.project(status=VideoProject.Status.PARTIAL)
        self.clip(project, 'Gol', data=b'A' * 3000, minutes=1)
        self.clip(project, 'Gol', data=b'B' * 10, minutes=2)
        self.clip(project, 'Publicado', status='published', data=b'C', minutes=3)
        self.clip(project, 'Roto', status='failed', data=None)
        self.clip(project, 'Borrador', status='draft', data=None)

        with patch.object(clip_download, 'CHUNK_BYTES', 1024):
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['url'].startswith('http://testserver/media/exports/zips/'))
        [stored] = self.stored_zips()
        with zipfile.ZipFile(stored) as archive:
            self.assertEqual(archive.namelist(), ['gol.mp4', 'gol-2.mp4', 'publicado.mp4'])
            self.assertEqual(archive.read('gol.mp4'), b'A' * 3000)
            self.assertEqual(archive.getinfo('gol.mp4').compress_type, zipfile.ZIP_STORED)
        self.assert_no_temp_files()

    def test_clip_ids_choose_which_clips(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        first = self.clip(project, 'Uno', minutes=1)
        self.clip(project, 'Dos', minutes=2)
        draft = self.clip(project, 'Tres', status='draft', data=None)

        response = self.client.post(zip_url(project), {'clip_ids': [str(first.pk), str(draft.pk)]}, format='json')

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(self.stored_zips()[0]) as archive:
            self.assertEqual(archive.namelist(), ['uno.mp4'])

    def test_nothing_ready_is_a_409(self):
        empty = self.project()
        self.assertEqual(self.client.post(zip_url(empty), {}, format='json').status_code, 409)
        rendering = self.project(status=VideoProject.Status.RENDERING)
        self.clip(rendering, status='rendering', data=None)
        response = self.client.post(zip_url(rendering), {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'Ningún clip está listo todavía.')

    def test_bad_clip_ids_are_a_400(self):
        project = self.project()
        for value in ('abc', ['nope'], {'a': 1}):
            with self.subTest(value=value):
                response = self.client.post(zip_url(project), {'clip_ids': value}, format='json')
                self.assertEqual(response.status_code, 400)

    @override_settings(CLIPS_ZIP_MAX_BYTES=100)
    def test_over_the_size_cap_is_refused_before_reading_anything(self):
        project = self.project()
        self.clip(project, 'Uno', data=b'x' * 60, minutes=1)
        self.clip(project, 'Dos', data=b'x' * 60, minutes=2)

        with patch.object(clip_download, '_open_chunks') as read:
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'zip_too_large')
        self.assertEqual(response.json()['limit'], 100)
        read.assert_not_called()
        self.assertEqual(self.stored_zips(), [])
        self.assert_no_temp_files()

    @override_settings(CLIPS_ZIP_MAX_BYTES=100)
    def test_bytes_over_the_cap_while_streaming_stop_the_zip(self):
        project = self.project()
        self.clip(project, 'Uno', data=b'x' * 150)
        with patch.object(clip_download, '_object_size', return_value=10):  # the size lied
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 422)
        self.assert_no_temp_files()

    @override_settings(CLIPS_ZIP_MAX_CLIPS=2)
    def test_over_the_clip_cap_is_refused(self):
        project = self.project()
        for n in range(3):
            self.clip(project, f'C{n}', minutes=n)
        response = self.client.post(zip_url(project), {}, format='json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'zip_too_many_clips')

    @override_settings(CLIPS_ZIP_MAX_SECONDS=-1)
    def test_past_the_time_cap_the_zip_stops_and_leaves_nothing(self):
        project = self.project()
        self.clip(project, 'Uno')

        response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'zip_timeout')
        self.assertEqual(self.stored_zips(), [])
        self.assert_no_temp_files()

    def test_another_zip_of_the_workspace_in_progress_is_a_429(self):
        project = self.project()
        self.clip(project)
        cache.add(f'clips-zip-lock:{self.home.pk}', 1, 60)

        response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 429)
        cache.delete(f'clips-zip-lock:{self.home.pk}')
        self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)
        # The lock goes when the request ends.
        self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)

    def test_cross_workspace_is_a_404_and_a_lector_downloads(self):
        foreign = self.project(workspace=self.team, user=self.boss)
        self.clip(foreign)
        self.assertEqual(self.client.post(zip_url(foreign), {}, format='json').status_code, 404)
        self.assertEqual(self.client.post(zip_url(foreign), {}, format='json', **header(self.team)).status_code, 404)

        join(self.team, self.alice, VIEWER)
        response = self.client.post(zip_url(foreign), {}, format='json', **header(self.team))
        self.assertEqual(response.status_code, 200)

    def test_expired_zips_are_deleted_by_the_next_one(self):
        project = self.project()
        self.clip(project)
        old = write_media('exports/zips/2020010100/old.zip', b'z')
        os.utime(old, (0, 0))

        self.client.post(zip_url(project), {}, format='json')

        self.assertFalse(os.path.exists(old))
        self.assertEqual(len(self.stored_zips()), 1)

    @R2
    def test_in_r2_clips_are_streamed_in_chunks_and_the_zip_uploaded_and_signed(self):
        project = self.project(title='Mi Podcast')
        for n, key in enumerate(('videos/a.mp4', 'videos/b.mp4')):
            VideoClip.objects.create(project=project, title=f'Parte {n}', start_time=0, end_time=1,
                                     status='completed', s3_object_key=key)
        fake = FakeR2({'videos/a.mp4': b'a' * 2500, 'videos/b.mp4': b'b' * 10})
        fake.listed = [
            {'Key': 'exports/zips/2020010100/old.zip', 'LastModified': timezone.now() - timedelta(days=2)},
            {'Key': 'exports/zips/2099010100/new.zip', 'LastModified': timezone.now()},
        ]
        with patch.object(clip_download, '_client', return_value=fake), patch.object(clip_download, 'CHUNK_BYTES', 1000):
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 200)
        [(key, (data, extra))] = fake.uploads.items()
        self.assertTrue(key.startswith('exports/zips/'))
        self.assertEqual(extra['ContentType'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read('parte-0.mp4'), b'a' * 2500)
        self.assertEqual(fake.bodies[0].chunk_sizes, [1000, 1000, 1000])
        self.assertTrue(all(body.closed for body in fake.bodies))
        params, _ttl = fake.presigned[0]
        self.assertEqual(params['Key'], key)
        self.assertEqual(params['ResponseContentDisposition'], 'attachment; filename="mi-podcast.zip"')
        self.assertEqual(fake.deleted, ['exports/zips/2020010100/old.zip'])
        self.assert_no_temp_files()

    @R2
    def test_a_storage_error_is_a_503_and_leaves_nothing_behind(self):
        project = self.project()
        VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                 s3_object_key='videos/a.mp4')
        cases = {
            'head': FakeR2({'videos/a.mp4': b'a'}, fail='HeadObject'),
            'missing': FakeR2({}),
            'read': FakeR2({'videos/a.mp4': b'a' * 5000}, fail_after=1000),
            'upload': FakeR2({'videos/a.mp4': b'a'}, fail='PutObject'),
            'sign': FakeR2({'videos/a.mp4': b'a'}, fail='Presign'),
        }
        for name, fake in cases.items():
            with self.subTest(name), patch.object(clip_download, '_client', return_value=fake), \
                    patch.object(clip_download, 'CHUNK_BYTES', 1000), \
                    self.assertLogs('apps.videos', 'WARNING') as logs:
                response = self.client.post(zip_url(project), {}, format='json')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['code'], 'storage_unavailable')
                self.assertNotIn('SECRET_LOG_X', '\n'.join(logs.output))
                self.assert_no_temp_files()
                if name == 'sign':
                    # Stored, then the signature failed: the upload is deleted.
                    self.assertEqual(fake.deleted, list(fake.uploads))
