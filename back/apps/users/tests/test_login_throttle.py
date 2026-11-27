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


# A fixed instant, 37 s into a 15-minute window (and into its hour).
NOW = 1_800_000_037.0


@pytest.fixture(autouse=True)
def frozen_clocks():
    """
    Time does not pass during a test of this file, however slow the machine
    is: these tests count attempts inside windows (a minute per IP, 15
    minutes and an hour per account) and inside the 5 s cooldown of the
    cache's circuit breaker. On a loaded runner a handful of logins (each one
    hashes a password) took longer than a cooldown, or than the minute.

    - the breaker's clock (apps.core.cache._now);
    - DRF's throttle timer (bound to time.time when the class was defined);
    - time.time, which LoginFailureGuard and LocMem's expiry read.

    A test that needs time to move patches time.time again inside.
    """
    with mock.patch("apps.core.cache._now", return_value=1000.0), \
            mock.patch.object(ScopedRateThrottle, "timer", return_value=NOW), \
            mock.patch("time.time", return_value=NOW):
        yield


def _wait_seconds(response):
    """The wait the 429 announces, back in seconds (the window ends at a fixed time)."""
    number, unit = re.search(r"en (\d+) (segundos?|minutos?)", response.json()["detail"]).groups()
    return int(number) * (1 if unit.startswith("segundo") else 60)


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
    assert 0 < _wait_seconds(blocked) <= 15 * 60
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
    assert 0 < _wait_seconds(blocked) <= 60 * 60


def test_the_token_refresh_is_limited_per_ip():
    api = _from("10.0.3.1")

    answers = [api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json").status_code for _ in range(30)]
    blocked = api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json")

    assert answers == [401] * 30
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos.")


VARIANTS = ["teſter@test.com", "tester@teſt.com", "TESTER@test.com"]


@override_settings(LOGIN_FAILURE_RATES={"account_ip": "3/15m", "account": "3/h"})
@pytest.mark.parametrize("variant", VARIANTS)
def test_another_spelling_of_a_locked_email_is_locked_too(user, no_ip_limit, variant):
    failures = [_login(_from(f"10.0.8.{n}")).status_code for n in range(3)]

    # The variant signs into the same account (iexact), so it shares the account's counters.
    blocked = _login(_from("10.0.9.1"), email=variant, password="password")

    assert failures == [401] * 3
    assert blocked.status_code == 429


@override_settings(LOGIN_FAILURE_RATES={"account_ip": "3/15m", "account": "3/h"})
@pytest.mark.parametrize("variant", VARIANTS)
def test_failures_with_another_spelling_lock_the_account(user, no_ip_limit, variant):
    failures = [_login(_from(f"10.0.10.{n}"), email=variant).status_code for n in range(3)]

    plain = _login(_from("10.0.11.1"), password="password")

    assert failures == [401] * 3
    assert plain.status_code == 429


# The `default` cache of production (apps.core.cache), pointed at a Redis that refuses the connection.
REDIS_DOWN = {
    "default": {
        "BACKEND": "apps.core.cache.FailOpenRedisCache",
        "LOCATION": "redis://127.0.0.1:6399/1",
        "OPTIONS": {"SOCKET_CONNECT_TIMEOUT": 2, "SOCKET_TIMEOUT": 2, "IGNORE_EXCEPTIONS": True},
    },
    "locks": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "down-locks"},
}


def _refused():
    return mock.patch("redis.connection.Connection._connect", side_effect=ConnectionRefusedError(111, "refused"))


def test_with_redis_down_the_account_limit_still_holds_in_this_process(user, no_ip_limit):
    with override_settings(CACHES=REDIS_DOWN), _refused():
        failures = [_login(_from("10.0.12.1")).status_code for _ in range(12)]
        blocked = _login(_from("10.0.12.1"), password="password")
        elsewhere = _login(_from("10.0.12.2"), password="password")

    # Counted in this process's memory: no 500, and no unlimited guessing either.
    assert failures == [401] * 10 + [429] * 2
    assert blocked.status_code == 429
    assert 0 < _wait_seconds(blocked) <= 15 * 60
    # The owner still signs in from another address.
    assert elsewhere.status_code == 200


def test_with_redis_down_a_successful_login_clears_the_failures(user, no_ip_limit):
    api = _from("10.0.12.3")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        first = [_login(api).status_code for _ in range(9)]
        ok = _login(api, password="password")
        second = [_login(api).status_code for _ in range(10)]
        blocked = _login(api, password="password")

    # 19 failures and no lock before the 20th: the good login cleared the first nine.
    assert first == [401] * 9
    assert ok.status_code == 200
    assert second == [401] * 10
    # And they are being counted: the limit comes at its number.
    assert blocked.status_code == 429


def test_with_redis_down_the_lock_ends_with_its_window(user, no_ip_limit):
    api = _from("10.0.12.4")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        with mock.patch("apps.users.throttles.time.time", return_value=900 * 1000.0):
            locked = [_login(api).status_code for _ in range(11)]
        # The next 15-minute window: the counts of the outage do not follow.
        with mock.patch("apps.users.throttles.time.time", return_value=900 * 1001.0):
            later = _login(api, password="password")

    assert locked == [401] * 10 + [429]
    assert later.status_code == 200


def test_requests_that_never_reach_the_password_check_do_not_count_with_redis_down(user, no_ip_limit):
    api = _from("10.0.12.5")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        invalid = [api.post(LOGIN_URL, {"email": "tester@test.com"}, format="json").status_code for _ in range(12)]
        failures = [_login(api).status_code for _ in range(10)]
        blocked = _login(api, password="password")

    assert invalid == [400] * 12
    # The twelve 400s were given back: the ten failures that follow all reach the password check.
    assert failures == [401] * 10
    assert blocked.status_code == 429


def test_with_redis_down_emails_without_an_account_get_no_counter_in_memory(user, no_ip_limit):
    from apps.users.throttles import local_counts

    api = _from("10.0.12.7")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        unknown = [_login(api, email=f"nadie{n}@test.com").status_code for n in range(30)]
        assert len(local_counts._cache) == 0
        known = _login(api)
        # Only the account's two windows (per IP and in total).
        assert len(local_counts._cache) == 2

    assert unknown == [401] * 30
    assert known.status_code == 401


def test_made_up_emails_cannot_push_an_account_out_of_the_memory(user, no_ip_limit):
    from apps.users import throttles

    small = throttles.LocMemCache("small-login-fallback", {"OPTIONS": {"MAX_ENTRIES": 4}})
    small.clear()
    attacker = _from("10.0.12.8")
    with override_settings(CACHES=REDIS_DOWN), _refused(), mock.patch.object(throttles, "local_counts", small):
        failures = []
        for n in range(10):
            failures.append(_login(attacker).status_code)
            # Between guesses, logins with emails nobody has: they would fill a store of 4 keys.
            for m in range(6):
                _login(attacker, email=f"relleno{n}-{m}@test.com")
        blocked = _login(attacker, password="password")

    assert failures == [401] * 10
    assert blocked.status_code == 429


def test_with_redis_down_the_login_is_still_limited_per_ip(user):
    api = _from("10.0.12.9")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [_login(api, email=f"nadie{n}@test.com").status_code for n in range(10)]
        blocked = _login(api, password="password")
        elsewhere = _login(_from("10.0.12.10"), password="password")

    assert answers == [401] * 10
    assert blocked.status_code == 429
    assert int(blocked["Retry-After"]) > 0
    assert elsewhere.status_code == 200


def test_with_redis_down_the_token_refresh_is_still_limited_per_ip():
    api = _from("10.0.12.11")
    with override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json").status_code for _ in range(31)]

    assert answers == [401] * 30 + [429]


def test_with_redis_down_guessing_the_current_password_is_still_throttled(user):
    api = APIClient()
    api.force_authenticate(user=user)
    payload = {"current_password": "otra", "new_password": "una-clave-Nueva-2026"}
    with override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [api.post("/api/v1/me/password/", payload, format="json").status_code for _ in range(6)]

    assert answers == [400] * 5 + [429]


def test_with_redis_down_email_changes_are_still_throttled_and_other_edits_are_not(user):
    api = APIClient()
    api.force_authenticate(user=user)
    payload = {"email": "nueva@test.com", "current_password": "otra"}
    with override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [api.patch("/api/v1/me/", payload, format="json").status_code for _ in range(6)]
        edits = [api.patch("/api/v1/me/", {"tour_completed": True}, format="json").status_code for _ in range(8)]

    assert answers == [400] * 5 + [429]
    assert edits == [200] * 8


def test_an_outage_is_logged_once_and_redis_is_tried_once_per_cooldown(user, caplog, no_ip_limit):
    api = _from("10.0.12.6")
    with override_settings(CACHES=REDIS_DOWN), _refused() as connect, caplog.at_level("INFO", logger="apps.core.cache"):
        answers = [_login(api).status_code for _ in range(5)]

    assert answers == [401] * 5
    # Five logins, each with several cache calls, all inside one cooldown (the
    # breaker's clock is frozen): one connection attempt, one log line.
    assert connect.call_count == 1
    assert [r.getMessage() for r in caplog.records if r.name == "apps.core.cache"] == ["cache.unavailable error=ConnectionError"]


def test_after_the_cooldown_redis_is_tried_once_more_and_the_outage_is_not_logged_again(user, caplog, no_ip_limit):
    api = _from("10.0.12.12")
    with override_settings(CACHES=REDIS_DOWN), _refused() as connect, caplog.at_level("INFO", logger="apps.core.cache"):
        first = [_login(api).status_code for _ in range(2)]
        # The cooldown (5 s) ends: the next login's first cache call probes Redis, the rest skip it.
        with mock.patch("apps.core.cache._now", return_value=1006.0):
            second = [_login(api).status_code for _ in range(2)]

    assert first == second == [401] * 2
    assert connect.call_count == 2
    assert [r.getMessage() for r in caplog.records if r.name == "apps.core.cache"] == ["cache.unavailable error=ConnectionError"]


def test_a_request_that_never_reached_the_password_check_does_not_count(user, no_ip_limit):
    api = _from("10.0.13.1")

    invalid = [api.post(LOGIN_URL, {"email": "tester@test.com"}, format="json").status_code for _ in range(12)]

    assert invalid == [400] * 12
    assert _login(api, password="password").status_code == 200
