"""Attempt limits of the login (/api/token/) and of the token refresh."""
import re
from unittest import mock

import pytest
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/token/"
REFRESH_URL = "/api/token/refresh/"
# The login screen reads the wait from the message (front/src/features/acceso/lib.ts · retryAfterSeconds).
WAIT_IN_SECONDS = re.compile(r"(\d+)\s*segundos?")


def _from(ip):
    return APIClient(REMOTE_ADDR=ip)


def _login(api, email="tester@test.com", password="otra"):
    return api.post(LOGIN_URL, {"email": email, "password": password}, format="json")


def test_the_login_is_limited_per_ip(user):
    api = _from("10.0.0.1")

    answers = [_login(api, email=f"nadie{n}@test.com").status_code for n in range(10)]
    blocked = _login(api, password="password")

    assert answers == [401] * 10
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail.startswith("Demasiados intentos. Probá de nuevo en ")
    assert 0 < int(WAIT_IN_SECONDS.search(detail).group(1)) <= 60
    assert int(blocked["Retry-After"]) > 0
    # Another address still signs in.
    assert _login(_from("10.0.0.2"), password="password").status_code == 200


def test_a_forged_x_forwarded_for_does_not_escape_the_ip_limit(user):
    # Without a proxy in front (NUM_PROXIES=0, the default outside production) the header is ignored.
    codes = [
        APIClient(REMOTE_ADDR="10.9.9.9", HTTP_X_FORWARDED_FOR=f"1.2.3.{n}").post(
            LOGIN_URL, {"email": f"nadie{n}@test.com", "password": "x"}, format="json",
        ).status_code
        for n in range(11)
    ]

    assert codes == [401] * 10 + [429]


@pytest.fixture
def no_ip_limit():
    """The per-IP limit (10/min) out of the way, to look at the per-account one."""
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"}):
        yield


def test_successful_logins_never_lock_the_account(user):
    answers = [_login(_from(f"10.0.1.{n}"), password="password").status_code for n in range(25)]

    assert answers == [200] * 25


def test_failures_from_one_ip_lock_the_account_only_there(user, no_ip_limit):
    attacker = _from("10.0.4.1")

    failures = [_login(attacker).status_code for _ in range(10)]
    blocked = _login(attacker, password="password")

    assert failures == [401] * 10
    assert blocked.status_code == 429
    assert "15 minutos" in blocked.json()["detail"]
    # The owner, somewhere else, still signs in.
    assert _login(_from("10.0.4.2"), password="password").status_code == 200
    # Other accounts from the attacker's address are not affected.
    assert _login(attacker, email="otra@test.com").status_code == 401


def test_a_successful_login_clears_the_failures(user, no_ip_limit):
    api = _from("10.0.5.1")

    first = [_login(api).status_code for _ in range(9)]
    assert _login(api, password="password").status_code == 200
    second = [_login(api).status_code for _ in range(9)]

    assert first == second == [401] * 9
    assert _login(api, password="password").status_code == 200


@override_settings(LOGIN_FAILURE_RATES={"account_ip": "10/15m", "account": "5/h"})
def test_failures_spread_over_many_ips_lock_the_account_everywhere(user):
    failures = [_login(_from(f"10.0.6.{n}")).status_code for n in range(5)]
    blocked = _login(_from("10.0.7.1"), password="password")

    assert failures == [401] * 5
    assert blocked.status_code == 429
    assert "60 minutos" in blocked.json()["detail"]


def test_the_token_refresh_is_limited_per_ip():
    api = _from("10.0.3.1")

    answers = [api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json").status_code for _ in range(30)]
    blocked = api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json")

    assert answers == [401] * 30
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos.")
