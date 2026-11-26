"""Attempt limits of the login (/api/token/) and of the token refresh."""
import re

import pytest
from rest_framework.test import APIClient

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


def test_the_login_is_limited_per_account_from_any_ip(user):
    answers = [_login(_from(f"10.0.1.{n}")).status_code for n in range(20)]
    blocked = _login(_from("10.0.2.1"), email="Tester@Test.com ", password="password")

    assert answers == [401] * 20
    assert blocked.status_code == 429
    # Other accounts are not affected.
    assert _login(_from("10.0.2.2"), email="otra@test.com").status_code == 401


def test_the_token_refresh_is_limited_per_ip():
    api = _from("10.0.3.1")

    answers = [api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json").status_code for _ in range(30)]
    blocked = api.post(REFRESH_URL, {"refresh": "no-es-un-token"}, format="json")

    assert answers == [401] * 30
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos.")
