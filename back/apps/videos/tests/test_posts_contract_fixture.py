"""
The scheduled-post bodies of front/src/api/contracts/drf/responses.json
(`posts_*` and the `post_*` errors) are what this backend answers: the
frontend parses them with its zod schemas (drf.test.ts), and this test builds
the same scenario through the API, with the clock frozen, and compares every
value with the file. Only the ids are normalised (``<idN>`` by the order they
first appear).

When a response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/videos/tests/test_posts_contract_fixture.py -s``
prints them.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments.models import SubscriptionPlan
from apps.payments.tests.test_contract_fixture import FIXTURE, normalised
from apps.users.models import User, Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject

BODIES = (
    'posts_create_draft', 'posts_create_scheduled', 'posts_create_now', 'posts_bulk', 'posts_update',
    'posts_publish_draft', 'posts_retry', 'posts_retry_seo_failed', 'posts_regenerate_seo', 'posts_list',
)
ERRORS = (
    'post_400_bulk_index', 'post_400_tiktok', 'post_403_scheduling', 'post_403_crossposting', 'post_403_seo',
    'post_409_account_not_connected', 'post_409_in_flight', 'post_409_published', 'post_409_not_failed',
    'post_409_idempotency_key_reused',
)
# The moment the scenario starts: November 27th, 12:00 in Buenos Aires.
NOW = datetime(2026, 11, 27, 15, 0, tzinfo=dt_timezone.utc)
TIKTOK = {'privacy_level': 'PUBLIC_TO_EVERYONE', 'allow_comment': True}


@contextmanager
def at(minutes):
    with patch('django.utils.timezone.now', return_value=NOW + timedelta(minutes=minutes)):
        yield


class PostsFixtureHarness:
    def real_bodies(self):
        with patch('apps.videos.tasks.process_video_seo.delay'), patch('apps.videos.tasks.process_video_seo.apply_async'):
            return self._real_bodies()

    def _real_bodies(self):
        ana = User.objects.create_user(username='ana', email='ana@test.com', password='pw')
        pro = SubscriptionPlan.objects.get(name='Pro')
        home = Workspace.objects.create(
            name='Estudio Norte', owner=ana, subscription_plan=pro, ayrshare_profile_key='perfil-estudio',
        )
        free = Workspace.objects.create(
            name='Gratis', owner=ana, subscription_plan=SubscriptionPlan.objects.get(name='Gratis'),
            ayrshare_profile_key='perfil-gratis',
        )

        def clip(workspace, title, clip_title, seo):
            project = VideoProject.objects.create(
                workspace=workspace, uploaded_by=ana, title=title, source_file='videos/raw/a.mp4',
                metadata={'use_seo': True} if seo else {},
            )
            return VideoClip.objects.create(
                project=project, title=clip_title, start_time=12.0, end_time=42.5,
                status=VideoClip.Status.COMPLETED, s3_object_key='clips/a.mp4',
            )

        remontada = clip(home, 'Entrevista con Ana', 'La remontada', seo=True)
        intro = clip(home, 'Podcast 12', 'Intro del podcast', seo=False)
        cierre = clip(home, 'Podcast 12 (cierre)', 'El cierre', seo=False)
        gratis = clip(free, 'Vlog Mendoza', 'Llegada', seo=False)

        self.client.force_authenticate(ana)
        ws = {'HTTP_X_WORKSPACE_ID': str(home.pk)}
        free_ws = {'HTTP_X_WORKSPACE_ID': str(free.pk)}
        posts = reverse('scheduled-post-list')
        bulk = reverse('scheduled-post-bulk')

        def call(method, url, body=None, headers=ws, minutes=0):
            with at(minutes):
                return getattr(self.client, method)(url, body or {}, format='json', **headers)

        def ok(response, code):
            assert response.status_code == code, response.content
            return response.json()

        def err(response, code):
            assert response.status_code == code, response.content
            return {'status': response.status_code, 'body': response.json()}

        tomorrow = (NOW + timedelta(days=1)).isoformat()
        # Every post at its own time: the list breaks ties by id, which changes on each run.
        draft = ok(call('post', posts, {'video_clip': str(remontada.pk), 'platform': 'TIKTOK', 'draft': True}), 201)
        scheduled = ok(call('post', posts, {
            'video_clip': str(intro.pk), 'platform': 'INSTAGRAM_REELS', 'publish_at': tomorrow,
            'caption': 'Arrancó el podcast 12', 'hashtags': ['#podcast', 'charlas'],
        }, minutes=1), 201)
        now = ok(call('post', posts, {
            'video_clip': str(intro.pk), 'platform': 'YOUTUBE_SHORTS', 'publish_at': None, 'caption': 'Ya salió',
        }, minutes=2), 201)
        batch = ok(call('post', bulk, {'posts': [
            {'video_clip': str(cierre.pk), 'platform': 'INSTAGRAM_REELS', 'publish_at': tomorrow, 'caption': 'El cierre'},
            {'video_clip': str(cierre.pk), 'platform': 'TIKTOK', 'publish_at': (NOW + timedelta(days=1, hours=1)).isoformat(),
             'tiktok': TIKTOK},
        ]}, minutes=3), 201)
        updated = ok(call('patch', reverse('scheduled-post-detail', args=[scheduled['id']]), {
            'publish_at': (NOW + timedelta(days=2)).isoformat(), 'caption': 'Arrancó el podcast 12: no te lo pierdas',
        }, minutes=4), 200)
        ScheduledPost.objects.filter(pk=draft['id']).update(
            generated_caption='La remontada más épica del año', generated_hashtags=['futbol', 'remontada', 'viral'],
            seo_requested_at=None,
        )
        published = ok(call('patch', reverse('scheduled-post-detail', args=[draft['id']]), {
            'publish_at': None, 'tiktok': TIKTOK,
        }, minutes=5), 200)

        with at(6):
            failed = ScheduledPost.objects.create(
                video_clip=remontada, platform='INSTAGRAM_REELS', status=ScheduledPost.Status.FAILED,
                publish_at=NOW - timedelta(hours=1), error_log='Failed after 4 attempts: upstream', retry_count=4,
            )
            seo_failed = ScheduledPost.objects.create(
                video_clip=remontada, platform='YOUTUBE_SHORTS', status=ScheduledPost.Status.FAILED,
                publish_at=NOW + timedelta(days=1, hours=3), error_log='seo_no_clip_transcript',
            )
        retried = ok(call('post', reverse('scheduled-post-retry', args=[failed.pk]), minutes=6), 200)
        retried_seo = ok(call('post', reverse('scheduled-post-retry', args=[seo_failed.pk]), minutes=7), 200)
        with at(8):
            ready = ScheduledPost.objects.create(
                video_clip=remontada, platform='TIKTOK', status=ScheduledPost.Status.DRAFT, publish_at=NOW,
                generated_caption='Mirá cómo lo dieron vuelta', generated_hashtags=['futbol', 'remontada'],
            )
        regenerated = ok(call('post', reverse('scheduled-post-regenerate-seo', args=[ready.pk]), minutes=9), 202)
        listed = ok(call('get', posts, minutes=10), 200)

        with at(11):
            queued = ScheduledPost.objects.create(
                video_clip=intro, platform='TIKTOK', status=ScheduledPost.Status.QUEUED, publish_at=NOW, tiktok=TIKTOK,
            )
            gone = ScheduledPost.objects.create(
                video_clip=cierre, platform='YOUTUBE_SHORTS', status=ScheduledPost.Status.PUBLISHED, publish_at=NOW,
            )
        errors = {
            'post_400_bulk_index': err(call('post', bulk, {'posts': [
                {'video_clip': str(intro.pk), 'platform': 'TIKTOK', 'draft': True},
                {'video_clip': str(intro.pk), 'platform': 'INSTAGRAM_REELS', 'caption': 'x' * 2201},
            ]}), 400),
            'post_400_tiktok': err(call('post', posts, {'video_clip': str(cierre.pk), 'platform': 'TIKTOK'}), 400),
            'post_403_scheduling': err(call('post', posts, {
                'video_clip': str(gratis.pk), 'platform': 'TIKTOK', 'publish_at': tomorrow, 'tiktok': TIKTOK,
            }, headers=free_ws), 403),
            'post_403_crossposting': self._crossposting(call, posts, gratis, free_ws, err),
            'post_403_seo': err(call('post', reverse('scheduled-post-regenerate-seo', args=[now['id']])), 403),
            'post_409_account_not_connected': self._no_account(call, posts, home, cierre, err),
            'post_409_in_flight': err(call('patch', reverse('scheduled-post-detail', args=[queued.pk]), {'caption': 'x'}), 409),
            'post_409_published': err(call('post', reverse('scheduled-post-cancel', args=[gone.pk])), 409),
            'post_409_not_failed': err(call('post', reverse('scheduled-post-retry', args=[retried['id']])), 409),
            'post_409_idempotency_key_reused': self._key_reused(call, posts, cierre, err, ws),
        }
        return {
            'posts_create_draft': draft,
            'posts_create_scheduled': scheduled,
            'posts_create_now': now,
            'posts_bulk': batch,
            'posts_update': updated,
            'posts_publish_draft': published,
            'posts_retry': retried,
            'posts_retry_seo_failed': retried_seo,
            'posts_regenerate_seo': regenerated,
            'posts_list': listed,
            'errors': errors,
        }

    @staticmethod
    def _crossposting(call, posts, gratis, free_ws, err):
        assert call('post', posts, {'video_clip': str(gratis.pk), 'platform': 'INSTAGRAM_REELS'}, headers=free_ws).status_code == 201
        return err(call('post', posts, {'video_clip': str(gratis.pk), 'platform': 'YOUTUBE_SHORTS'}, headers=free_ws), 403)

    @staticmethod
    def _no_account(call, posts, home, clip, err):
        Workspace.objects.filter(pk=home.pk).update(ayrshare_profile_key=None)
        try:
            return err(call('post', posts, {'video_clip': str(clip.pk), 'platform': 'YOUTUBE_SHORTS'}), 409)
        finally:
            Workspace.objects.filter(pk=home.pk).update(ayrshare_profile_key='perfil-estudio')

    @staticmethod
    def _key_reused(call, posts, clip, err, ws):
        first = {'video_clip': str(clip.pk), 'platform': 'YOUTUBE_SHORTS', 'draft': True}
        headers = {**ws, 'HTTP_IDEMPOTENCY_KEY': 'envio-1'}
        assert call('post', posts, first, headers=headers).status_code == 201
        return err(call('post', posts, {**first, 'caption': 'Otro'}, headers=headers), 409)


@pytest.mark.skipif(not FIXTURE.exists(), reason='front/ is not next to back/ (an image built from back/ alone)')
class PostsFixtureTests(PostsFixtureHarness, APITestCase):
    maxDiff = None

    def test_the_fixture_has_the_values_of_the_real_responses(self):
        real = self.real_bodies()
        if os.environ.get('PRINT_DRF_FIXTURE'):
            print(json.dumps(real, ensure_ascii=False, indent=2))
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

        def part(source):
            return {
                **{name: source.get(name) for name in BODIES},
                'errors': {name: source['errors'].get(name) for name in ERRORS},
            }

        expected, actual = normalised(part(fixture)), normalised(part(real))
        for name in BODIES:
            with self.subTest(body=name):
                self.assertEqual(expected[name], actual[name])
        for name in ERRORS:
            with self.subTest(error=name):
                self.assertEqual(expected['errors'][name], actual['errors'][name])
