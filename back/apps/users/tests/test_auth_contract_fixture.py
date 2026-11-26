"""
The account bodies of front/src/api/contracts/drf/responses.json are the ones
this backend gives: front/src/api/contracts/drf-cuentas.test.ts parses them with the
contract (zod) and with the web app's error helpers. This test builds each of
them with the real endpoints and compares. When an answer changes on purpose,
copy the body it prints into the JSON (only the keys of AUTH_KEYS and
AUTH_ERROR_KEYS; ids are compared by shape).
"""
import json
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

AUTH_KEYS = ["auth_register"]
AUTH_ERROR_KEYS = [
    "register_400_taken_and_weak",
    "register_400_terms",
    "resend_429",
    "password_reset_confirm_400_token",
    "password_reset_confirm_400_password",
    "logout_400_other_account",
    "session_revoked_401",
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
    taken = APIClient().post(
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
    link = next(word for word in mail.outbox[-1].body.split() if "/recuperar?" in word)
    token = parse_qs(urlparse(link).query)["token"][0]
    weak = APIClient().post("/api/v1/auth/password-reset/confirm/", {"token": token, "password": "12345678"}, format="json")
    mine = _login("tester@test.com")
    theirs = _login("ana@example.com", "una-clave-Nueva-2026")
    other = _client(mine["access"]).post("/api/v1/auth/logout/", {"refresh": theirs["refresh"]}, format="json")
    phone = _login("tester@test.com")
    assert _client(mine["access"]).post("/api/v1/auth/logout/", {"refresh": mine["refresh"], "all_devices": True}, format="json").status_code == 204
    revoked = _client(phone["access"]).get("/api/v1/me/")
    return {
        "auth_register": register.json(),
        "errors": {
            "register_400_taken_and_weak": _error(taken),
            "register_400_terms": _error(terms),
            "resend_429": _error(resend),
            "password_reset_confirm_400_token": _error(bad_link),
            "password_reset_confirm_400_password": _error(weak),
            "logout_400_other_account": _error(other),
            "session_revoked_401": _error(revoked),
        },
    }


@pytest.mark.skipif(not FIXTURE.exists(), reason="Sin el frontend al lado (front/): nada que comparar.")
def test_the_account_bodies_of_the_contract_fixture_are_the_real_ones(user):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real = _real_bodies(user)

    # The id is new each time: a UUID in both, then the fixture's.
    uuid.UUID(real["auth_register"]["id"])
    uuid.UUID(fixture["auth_register"]["id"])
    real["auth_register"]["id"] = fixture["auth_register"]["id"]
    found = {
        **{key: fixture.get(key) for key in AUTH_KEYS},
        "errors": {key: fixture.get("errors", {}).get(key) for key in AUTH_ERROR_KEYS},
    }
    assert found == real, "Actualizá responses.json con:\n" + json.dumps(real, ensure_ascii=False, indent=2)
