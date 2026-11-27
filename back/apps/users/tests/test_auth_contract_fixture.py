"""
The account bodies of front/src/api/contracts/drf/responses.json are the ones
this backend gives: front/src/api/contracts/drf-cuentas.test.ts parses them
with the contract (zod) and with the web app's error helpers. This test builds
each of them with the real endpoints and compares every value (like the wallet
ones, apps/payments/tests/test_contract_fixture.py). The id and the
`signup_nonce` of the new account change on each run: they are compared by
shape (a UUID, a long string).

When an answer changes on purpose, copy the new bodies into the file (only the
keys of AUTH_KEYS and AUTH_ERROR_KEYS). The failure prints them, and so does
``PRINT_DRF_FIXTURE=1 python -m pytest apps/users/tests/test_auth_contract_fixture.py -s``.
"""
import json
import os
import time
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan
from apps.users.throttles import EmailAttemptThrottle

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[4] / "front" / "src" / "api" / "contracts" / "drf" / "responses.json"

AUTH_KEYS = ["auth_register", "verify_email_signed_out"]
AUTH_ERROR_KEYS = [
    "register_400_taken",
    "register_400_weak",
    "register_400_terms",
    "resend_429",
    "password_reset_confirm_400_token",
    "password_reset_confirm_400_password",
    "logout_400_other_account",
    "session_revoked_401",
    "refresh_401_session_revoked",
]


def _login(email, password="password"):
    return APIClient().post("/api/token/", {"email": email, "password": password}, format="json").json()


def _client(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api


def _error(response):
    return {"status": response.status_code, "body": response.json()}


def _real_bodies(user):
    SubscriptionPlan.objects.get_or_create(name=settings.DEFAULT_PLAN_NAME)
    register = APIClient().post(
        "/api/v1/auth/register/",
        {"name": "Ana Gómez", "email": "ana@example.com", "password": "una-clave-Nueva-2026", "terms_accepted": True},
        format="json",
    )
    assert register.status_code == 201
    # Without the nonce of the sign-up (another browser): it only verifies.
    signup_link = next(word for word in mail.outbox[-1].body.split() if "/verificar#" in word)
    signed_out = APIClient().post(
        "/api/v1/auth/verify-email/", {"token": parse_qs(urlparse(signup_link).fragment)["token"][0]}, format="json",
    )
    taken = APIClient().post(
        "/api/v1/auth/register/",
        {"name": "Ana", "email": "TESTER@test.com", "password": "una-clave-Nueva-2026", "terms_accepted": True},
        format="json",
    )
    # Weak with an email that has an account: the same answer as with any other email.
    weak_signup = APIClient().post(
        "/api/v1/auth/register/",
        {"name": "Ana", "email": "TESTER@test.com", "password": "12345678", "terms_accepted": True},
        format="json",
    )
    terms = APIClient().post(
        "/api/v1/auth/register/",
        {"name": "Ana", "email": "otra@example.com", "password": "una-clave-Nueva-2026", "terms_accepted": False},
        format="json",
    )
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=time.time()):
        APIClient().post("/api/v1/auth/verify-email/resend/", {"email": "nadie@example.com"}, format="json")
        resend = APIClient().post("/api/v1/auth/verify-email/resend/", {"email": "nadie@example.com"}, format="json")
    bad_link = APIClient().post("/api/v1/auth/password-reset/confirm/", {"token": "vencido", "password": "x"}, format="json")
    APIClient().post("/api/v1/auth/password-reset/", {"email": "tester@test.com"}, format="json")
    link = next(word for word in mail.outbox[-1].body.split() if "/recuperar#" in word)
    token = parse_qs(urlparse(link).fragment)["token"][0]
    weak = APIClient().post("/api/v1/auth/password-reset/confirm/", {"token": token, "password": "12345678"}, format="json")
    mine = _login("tester@test.com")
    theirs = _login("ana@example.com", "una-clave-Nueva-2026")
    other = _client(mine["access"]).post("/api/v1/auth/logout/", {"refresh": theirs["refresh"]}, format="json")
    phone = _login("tester@test.com")
    assert _client(mine["access"]).post("/api/v1/auth/logout/", {"refresh": mine["refresh"], "all_devices": True}, format="json").status_code == 204
    revoked = _client(phone["access"]).get("/api/v1/me/")
    # The other device whose access token had already expired: the refresh (blacklisted) says why too.
    revoked_refresh = APIClient().post("/api/token/refresh/", {"refresh": phone["refresh"]}, format="json")
    return {
        "auth_register": register.json(),
        "verify_email_signed_out": signed_out.json(),
        "errors": {
            "register_400_taken": _error(taken),
            "register_400_weak": _error(weak_signup),
            "register_400_terms": _error(terms),
            "resend_429": _error(resend),
            "password_reset_confirm_400_token": _error(bad_link),
            "password_reset_confirm_400_password": _error(weak),
            "logout_400_other_account": _error(other),
            "session_revoked_401": _error(revoked),
            "refresh_401_session_revoked": _error(revoked_refresh),
        },
    }


@pytest.mark.skipif(not FIXTURE.exists(), reason="Sin el frontend al lado (front/): nada que comparar.")
def test_the_account_bodies_of_the_contract_fixture_are_the_real_ones(user):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real = _real_bodies(user)
    if os.environ.get("PRINT_DRF_FIXTURE"):
        print(json.dumps(real, ensure_ascii=False, indent=2))

    # The id and the nonce are new each time: the same shape in both, then the fixture's.
    for created in (real["auth_register"], fixture["auth_register"]):
        uuid.UUID(created["id"])
        assert isinstance(created["signup_nonce"], str) and len(created["signup_nonce"]) >= 32
    real["auth_register"]["id"] = fixture["auth_register"]["id"]
    real["auth_register"]["signup_nonce"] = fixture["auth_register"]["signup_nonce"]
    found = {
        **{key: fixture.get(key) for key in AUTH_KEYS},
        "errors": {key: fixture.get("errors", {}).get(key) for key in AUTH_ERROR_KEYS},
    }
    assert found == real, "Actualizá responses.json con:\n" + json.dumps(real, ensure_ascii=False, indent=2)
