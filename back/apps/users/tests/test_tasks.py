"""The daily clean-up of expired refresh tokens (apps/users/tasks.py)."""
import logging
from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.tasks import flush_expired_tokens

pytestmark = pytest.mark.django_db


def _expire(token):
    OutstandingToken.objects.filter(jti=token["jti"]).update(expires_at=timezone.now() - timedelta(minutes=1))


def test_the_task_deletes_the_expired_tokens_and_logs_the_counts(user, caplog):
    expired, expired_and_rotated, live, live_and_rotated = (RefreshToken.for_user(user) for _ in range(4))
    expired_and_rotated.blacklist()
    live_and_rotated.blacklist()
    _expire(expired)
    _expire(expired_and_rotated)

    with caplog.at_level(logging.INFO, logger="apps.users.tasks"):
        counts = flush_expired_tokens.delay().get()

    assert counts == {"outstanding_deleted": 2, "blacklisted_deleted": 1, "outstanding_left": 2, "blacklisted_left": 1}
    assert set(OutstandingToken.objects.values_list("jti", flat=True)) == {live["jti"], live_and_rotated["jti"]}
    assert list(BlacklistedToken.objects.values_list("token__jti", flat=True)) == [live_and_rotated["jti"]]
    assert [record.getMessage() for record in caplog.records if record.name == "apps.users.tasks"] == [
        "tokens.expired_flushed outstanding_deleted=2 blacklisted_deleted=1 outstanding_left=2 blacklisted_left=1"
    ]


def test_the_sessions_that_have_not_expired_keep_working(user):
    live = RefreshToken.for_user(user)
    rotated = RefreshToken.for_user(user)
    rotated.blacklist()

    flush_expired_tokens()

    api = APIClient()
    assert api.post("/api/token/refresh/", {"refresh": str(live)}, format="json").status_code == 200
    assert api.post("/api/token/refresh/", {"refresh": str(rotated)}, format="json").status_code == 401


def test_beat_runs_it_once_a_day_at_a_quiet_hour():
    entry = settings.CELERY_BEAT_SCHEDULE["flush-expired-tokens"]

    assert entry["task"] == flush_expired_tokens.name
    assert entry["schedule"] == crontab(hour=4, minute=30)
