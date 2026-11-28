"""
The notification bodies of front/src/api/contracts/drf/responses.json are what
this backend answers: the frontend parses them with its zod schemas
(drf.test.ts) and shows them in the bell (NotificationsPanel.test.tsx), and
this test builds the same scenario through the real events' service, with the
clock frozen, and compares every value with the file. Only the ids are
normalised (``<idN>`` by the order they first appear).

When a response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/notifications/tests/test_contract_fixture.py -s``
prints them.
"""
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.notifications import services
from apps.payments.models import Payment
from apps.payments.services.payments import settle_payment
from apps.payments.services.wallet_service import reserve_funds, rollback_reservation
from apps.payments.tests.test_contract_fixture import FIXTURE, normalised
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tasks.social import _fail_post
from apps.videos.tests.test_ingestion_pipeline import fund_wallet

from .helpers import make_user, team

BODIES = ('notifications_list', 'notification_read')
ERRORS = ('notification_404',)
# The moment the scenario starts: November 4th, 12:00 in Buenos Aires.
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=dt_timezone.utc)


@contextmanager
def at(minutes):
    with patch('django.utils.timezone.now', return_value=NOW + timedelta(minutes=minutes)):
        yield


class NotificationsFixtureHarness:
    def real_bodies(self):
        """One notification of each kind the backend writes, the oldest one read, and the 404."""
        ana = make_user('ana')
        home = team(ana, 'Estudio Norte')
        wallet = fund_wallet(home, '1000.00')

        def project(title, status, clips=0):
            row = VideoProject.objects.create(workspace=home, uploaded_by=ana, title=title, source_file='a/b.mp4', status=status)
            for index in range(clips):
                VideoClip.objects.create(
                    project=row, title=f'Clip {index}', start_time=index, end_time=index + 5, status=VideoClip.Status.COMPLETED,
                )
            return row

        def purchase(tokens):
            return Payment.objects.create(
                workspace=home, created_by=ana, tokens=tokens, amount_usd=Decimal(tokens) / 100, provider='simulated',
            )

        @contextmanager
        def event(minutes):
            """What happens ``minutes`` after NOW, with its notification written then."""
            with at(minutes), self.captureOnCommitCallbacks(execute=True):
                yield

        with event(0):
            rendered = project('Entrevista con Ana', VideoProject.Status.COMPLETED, clips=4)
            services.notify_render_finished(rendered.pk)
        with event(10):
            failed = project('Charla TEDx', VideoProject.Status.FAILED)
            reservation = reserve_funds(wallet.id, Decimal('12.5'), project=failed)
            since = timezone.now()
            rollback_reservation(reservation.id)
            services.notify_project_failed(failed.pk, since=since)
        with event(20):
            settle_payment(purchase(1500).pk, approved=True)
        with event(30):
            settle_payment(purchase(5000).pk, approved=False)
        with event(40):
            ready = project('Podcast Ep. 42', VideoProject.Status.AWAITING_APPROVAL, clips=9)
            services.notify_project_ready(ready.pk)

        with event(50):
            # 1.000 + 1.500 of the purchase − 2.200: under the 600 of the plan's threshold.
            reserve_funds(wallet.id, Decimal('2200'), project=ready)

        with event(55):
            # A post of the rendered project that the publisher left FAILED without knowing if it went out.
            post = ScheduledPost.objects.create(
                video_clip=rendered.clips.order_by('start_time').first(), platform=ScheduledPost.Platform.TIKTOK,
                status=ScheduledPost.Status.QUEUED, publish_at=timezone.now(),
            )
            _fail_post(post.pk, error_log='outcome_unknown (ayrshare http=504)')

        self.client.force_authenticate(ana)
        ws = {'HTTP_X_WORKSPACE_ID': str(home.pk)}
        oldest = self.client.get(reverse('notification-list'), **ws).json()['results'][-1]
        with at(60):
            read = self.client.post(reverse('notification-read', args=[oldest['id']]), **ws)
        listed = self.client.get(reverse('notification-list'), **ws)
        missing = self.client.post(reverse('notification-read', args=[uuid.uuid4()]), **ws)
        assert (read.status_code, listed.status_code) == (200, 200)
        return {
            'notifications_list': listed.json(),
            'notification_read': read.json(),
            'errors': {'notification_404': {'status': missing.status_code, 'body': missing.json()}},
        }


@pytest.mark.skipif(not FIXTURE.exists(), reason='front/ is not next to back/ (an image built from back/ alone)')
class NotificationsFixtureTests(NotificationsFixtureHarness, APITestCase):
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
