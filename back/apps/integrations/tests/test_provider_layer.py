"""
What the second provider changes around it: the cached accounts stay readable
by the release before Account.tiktok (a deploy runs both), and production
refuses an Upload-Post base URL without https, as it does Ayrshare's.
"""
import os
import subprocess
import sys
from dataclasses import fields
from datetime import datetime, timezone as dt_timezone

import pytest

from apps.core.tests.test_prod_settings import BACK, PRODUCTION
from apps.integrations.social import Account, AccountStatus
from apps.integrations.social import accounts as social_accounts

CREATOR = {"privacy_options": ["SELF_ONLY"], "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}


def test_an_account_without_tiktok_options_is_cached_as_before():
    """An Ayrshare account (no `tiktok`) is stored with the four fields the previous release reads."""
    account = Account("TIKTOK", AccountStatus.CONNECTED, "@ana", datetime(2026, 11, 2, tzinfo=dt_timezone.utc))
    stored = social_accounts._stored(account)

    assert sorted(stored) == ["connected_at", "platform", "status", "username"]
    previous_release = [name for name in (field.name for field in fields(Account)) if name != "tiktok"]
    Account(**{name: stored[name] for name in previous_release})  # what Account(**item) did


def test_a_cached_account_is_read_whatever_release_wrote_it():
    account = Account("TIKTOK", AccountStatus.CONNECTED, "@ana", None, CREATOR)
    assert social_accounts._account(social_accounts._stored(account)) == account
    # Written by a later release with a field this one does not know.
    later = {**social_accounts._stored(account), "max_duration_seconds": 600}
    assert social_accounts._account(later) == account
    # Written by the previous release.
    assert social_accounts._account({"platform": "TIKTOK", "status": "expired", "username": None, "connected_at": None}) \
        == Account("TIKTOK", AccountStatus.EXPIRED)


@pytest.mark.django_db
def test_the_accounts_read_caches_them_as_the_previous_release_reads_them():
    """The call site in accounts(): what it keeps in the cache is _stored's, not asdict's."""
    from unittest.mock import patch

    from django.core.cache import cache

    from apps.integrations.models import SocialProfile
    from apps.users.models import User

    workspace = User.objects.create_user(username="cache", email="cache@test.com", password="pw").owned_workspaces.get()
    SocialProfile.objects.create(workspace=workspace, provider="ayrshare", provider_ref="KEY")
    found = [Account("TIKTOK", AccountStatus.CONNECTED, "@ana"), Account("INSTAGRAM_REELS", AccountStatus.DISCONNECTED),
             Account("YOUTUBE_SHORTS", AccountStatus.DISCONNECTED)]
    with patch("apps.integrations.social.ayrshare.AyrshareProvider.accounts", return_value=found):
        social_accounts.accounts(workspace, fresh=True)
    for kind in ("accounts", "accounts_last"):
        stored = cache.get(f"social:{kind}:{workspace.pk}")
        assert [sorted(item) for item in stored] == [["connected_at", "platform", "status", "username"]] * 3, kind


def _load_prod(**overrides):
    if (BACK / ".env").exists():
        pytest.skip("back/.env would fill the missing variables")
    env = {key: value for key, value in os.environ.items()
           if key not in PRODUCTION and key not in ("AYRSHARE_BASE_URL", "UPLOAD_POST_BASE_URL")}
    env.update(PRODUCTION)
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.prod", **overrides})
    code = "import django; django.setup(); from django.conf import settings; print(settings.UPLOAD_POST_BASE_URL)"
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)


def test_production_takes_upload_posts_https_api():
    result = _load_prod()
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["https://api.upload-post.com"]

    stand_in = _load_prod(UPLOAD_POST_BASE_URL="https://upload-post-falso.example.com/")
    assert stand_in.returncode == 0, stand_in.stderr
    assert stand_in.stdout.split() == ["https://upload-post-falso.example.com"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8791", "ftp://api.upload-post.com", "https://", "api.upload-post.com"])
def test_production_refuses_an_upload_post_base_url_without_https(url):
    result = _load_prod(UPLOAD_POST_BASE_URL=url)
    assert result.returncode != 0
    assert "ImproperlyConfigured: UPLOAD_POST_BASE_URL must be an https URL in production" in result.stderr
