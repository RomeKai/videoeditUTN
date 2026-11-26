"""Sign-up (A2), the link that verifies the new account (A3) and resending it."""
import smtplib
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from kombu.exceptions import OperationalError as BrokerError
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.core.structured_logging import JsonFormatter
from apps.payments.models import SubscriptionPlan, Wallet
from apps.users import tasks
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.tests.test_login_throttle import REDIS_DOWN, _refused
from apps.users.throttles import EmailAttemptThrottle

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register/"
VERIFY_URL = "/api/v1/auth/verify-email/"
RESEND_URL = "/api/v1/auth/verify-email/resend/"
LOGIN_URL = "/api/token/"
ME_URL = "/api/v1/me/"
PASSWORD = "una-clave-Nueva-2026"
TAKEN = [{"message": "Ya existe una cuenta con este email.", "code": "unique"}]


def _register(api=None, **changes):
    body = {"name": "Ana Gómez", "email": "ana@example.com", "password": PASSWORD, "terms_accepted": True, **changes}
    return (api or APIClient()).post(REGISTER_URL, {k: v for k, v in body.items() if v is not None}, format="json")


def _signup_token(message):
    link = next(word for word in message.body.split() if "/verificar?" in word)
    query = parse_qs(urlparse(link).query)
    return query, link


@pytest.fixture
def free_plan_row(db):
    plan, _ = SubscriptionPlan.objects.get_or_create(name=settings.DEFAULT_PLAN_NAME)
    return plan


def test_signing_up_creates_the_account_its_workspace_and_sends_the_link(free_plan_row):
    response = _register()

    assert response.status_code == 201
    body = response.json()
    user = User.objects.get(email="ana@example.com")
    assert body == {"id": str(user.pk), "email": "ana@example.com", "is_email_verified": False}
    assert (user.first_name, user.last_name) == ("Ana", "Gómez")
    assert user.check_password(PASSWORD)
    assert user.terms_accepted_at is not None
    assert not user.is_email_verified
    # The sign-up path of User (signals): own workspace on the default plan, its wallet, the user as active Admin.
    workspace = Workspace.objects.get(owner=user)
    assert workspace.name == "Espacio de Ana"
    assert workspace.subscription_plan == free_plan_row
    assert Wallet.objects.filter(workspace=workspace).exists()
    assert WorkspaceMember.objects.filter(
        workspace=workspace, user=user, role=WorkspaceMember.Role.ADMIN, status=WorkspaceMember.Status.ACTIVE,
    ).exists()
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["ana@example.com"]
    query, link = _signup_token(message)
    assert link.startswith("http://localhost:3000/verificar?")
    # No `siguiente`: A3 goes to the page it remembered or to the welcome.
    assert set(query) == {"token"}
    assert "vence en 24 horas" in message.body
    # Nothing the person typed travels in the official email.
    assert "Ana" not in message.body and "Gómez" not in message.body


def test_the_link_verifies_the_account_and_signs_in_once(free_plan_row):
    _register()
    query, _ = _signup_token(mail.outbox[0])

    verified = APIClient().post(VERIFY_URL, {"token": query["token"][0]}, format="json")

    assert verified.status_code == 200
    tokens = verified.json()
    assert set(tokens) == {"access", "refresh"}
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = api.get(ME_URL).json()
    assert me["email"] == "ana@example.com"
    assert me["is_email_verified"] is True
    # Used once: the account is verified, the link no longer matches it.
    again = APIClient().post(VERIFY_URL, {"token": query["token"][0]}, format="json")
    assert again.status_code == 400
    assert again.json() == {"token": ["El link no es válido o ya venció."]}


@pytest.mark.parametrize("email", ["TESTER@test.com", "tester@ＴＥＳＴ.com", "Tester@TEST.COM"])
def test_an_email_with_an_account_under_any_spelling_is_refused(user, email):
    response = _register(email=email)

    assert response.status_code == 400
    assert response.json() == {"email": TAKEN}
    assert User.objects.count() == 1
    assert mail.outbox == []


def test_a_unicode_domain_and_its_punycode_are_the_same_email(free_plan_row):
    assert _register(email="ana@mañana.com").status_code == 201
    assert User.objects.get().email == "ana@xn--maana-pta.com"

    assert _register(email="ana@xn--MAANA-pta.com").json() == {"email": TAKEN}


def test_the_email_is_stored_normalized_and_signs_in_with_any_spelling(free_plan_row):
    assert _register(email="Ana@ＥＸＡＭＰＬＥ.com").status_code == 201

    assert User.objects.get().email == "Ana@example.com"
    login = APIClient().post(LOGIN_URL, {"email": "ana@example.com", "password": PASSWORD}, format="json")
    assert login.status_code == 200


@pytest.mark.parametrize("terms", [False, None, "no"])
def test_the_terms_must_be_accepted(terms):
    response = _register(terms_accepted=terms)

    assert response.status_code == 400
    assert response.json() == {"terms_accepted": ["Para crear la cuenta tenés que aceptar los términos."]}
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "changes, errors",
    [
        ({"password": "12345678"}, ["Esa contraseña es muy común. Elegí otra.", "La contraseña no puede ser solo números."]),
        ({"password": "corta"}, ["La contraseña tiene que tener 8 caracteres como mínimo."]),
        ({"email": "anagomez@example.com", "password": "anagomez@example"}, ["La contraseña se parece demasiado a tu nombre o a tu email."]),
    ],
)
def test_weak_passwords_get_the_rules_in_spanish(changes, errors):
    response = _register(**changes)

    assert response.status_code == 400
    assert response.json() == {"password": errors}
    assert not User.objects.exists()


def test_every_field_says_what_is_wrong_in_spanish():
    response = APIClient().post(REGISTER_URL, {"name": "", "email": "no-es-un-email"}, format="json")

    assert response.status_code == 400
    assert response.json() == {
        "name": ["Escribí tu nombre."],
        "email": ["Revisá el email: no parece completo."],
        "password": ["Elegí una contraseña."],
        "terms_accepted": ["Para crear la cuenta tenés que aceptar los términos."],
    }


def test_a_domain_that_cannot_be_encoded_is_a_field_error():
    response = _register(email="ana@℀.com")

    assert response.status_code == 400
    assert response.json() == {"email": ["El dominio del email no es válido. Revisá que esté bien escrito."]}


def test_an_email_server_that_fails_saves_nothing(free_plan_row, caplog):
    with mock.patch("apps.users.email_verification.send_mail", side_effect=smtplib.SMTPServerDisconnected("caído")), \
            caplog.at_level("WARNING", logger="apps.users.views_auth"):
        response = _register()

    assert response.status_code == 502
    assert response.json()["detail"] == "No pudimos mandar el email de verificación. Probá de nuevo en unos minutos."
    assert not User.objects.exists()
    assert not Workspace.objects.exists()
    assert [record.getMessage() for record in caplog.records if record.name == "apps.users.views_auth"] == [
        "register.send_failed error=SMTPServerDisconnected",
    ]


def test_an_address_the_email_server_refuses_is_a_field_error(free_plan_row):
    refused = smtplib.SMTPRecipientsRefused({"ana@example.com": (550, b"no such user")})
    with mock.patch("apps.users.email_verification.send_mail", side_effect=refused):
        response = _register()

    assert response.status_code == 400
    assert response.json() == {"email": ["El servidor de email rechazó esa dirección. Revisá que esté bien escrita."]}
    assert not User.objects.exists()


def test_a_token_in_the_header_is_not_read(free_plan_row):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer vencido")

    assert _register(api).status_code == 201


def test_signing_up_is_limited_per_ip(free_plan_row):
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"register": "3/hour"}):
        answers = [_register(APIClient(REMOTE_ADDR="10.1.0.1"), email=f"ana{n}@example.com").status_code for n in range(3)]
        # Also the ones that only learn the email is taken.
        blocked = _register(APIClient(REMOTE_ADDR="10.1.0.1"), email="ana0@example.com")
        elsewhere = _register(APIClient(REMOTE_ADDR="10.1.0.2"), email="otra@example.com")

    assert answers == [201] * 3
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos. Probá de nuevo en ")
    assert int(blocked["Retry-After"]) > 0
    assert elsewhere.status_code == 201


def test_with_redis_down_signing_up_is_still_limited(free_plan_row):
    api = APIClient(REMOTE_ADDR="10.1.0.3")
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"register": "3/hour"}), \
            override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [_register(api, email=f"ana{n}@example.com").status_code for n in range(4)]

    assert answers == [201] * 3 + [429]


# --- Resend the verification ---


def _resend(email, api=None):
    return (api or APIClient()).post(RESEND_URL, {"email": email}, format="json")


def _unverified(email="nuevo@example.com"):
    return User.objects.create_user(username=email, email=email, password=PASSWORD)


def test_resend_mails_a_new_link_to_an_unverified_account():
    account = _unverified()

    response = _resend("NUEVO@example.com")

    assert response.status_code == 204
    assert response.content == b""
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [account.email]
    query, _ = _signup_token(mail.outbox[0])
    verified = APIClient().post(VERIFY_URL, {"token": query["token"][0]}, format="json")
    assert verified.status_code == 200


def test_resend_answers_204_and_mails_nothing_without_an_unverified_account(user):
    User.objects.filter(pk=user.pk).update(is_email_verified=True)

    verified = _resend("tester@test.com")
    unknown = _resend("nadie@test.com")

    assert (verified.status_code, unknown.status_code) == (204, 204)
    assert mail.outbox == []


def test_the_request_does_the_same_work_with_or_without_an_account(user):
    """One query and one queued task either way; the email is sent by the worker, never in the request."""
    answers = []
    with mock.patch.object(tasks.send_account_email, "delay") as queued:
        for url in (RESEND_URL, "/api/v1/auth/password-reset/"):
            for email in ("TESTER@test.com", "nadie@test.com"):
                with CaptureQueriesContext(connection) as queries:
                    response = APIClient().post(url, {"email": email}, format="json")
                answers.append((response.status_code, response.content, response.get("Content-Type"), len(queries)))

    assert answers == [(204, b"", None, 1)] * 4
    # Only the id goes to the queue, never the address.
    assert [call.args for call in queued.call_args_list] == [
        ("resend_verification", str(user.pk)),
        ("resend_verification", None),
        ("password_reset", str(user.pk)),
        ("password_reset", None),
    ]
    assert mail.outbox == []


def test_resend_is_once_every_60_seconds_per_email_with_or_without_an_account():
    _unverified()
    start = time.time()
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=start):
        first = [_resend(email).status_code for email in ("nuevo@example.com", "nadie@example.com")]
        # Any spelling of the same address shares the wait.
        again = [_resend(email) for email in ("Nuevo@EXAMPLE.com", "nadie@ｅｘａｍｐｌｅ.com")]
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=start + 61):
        later = [_resend(email).status_code for email in ("nuevo@example.com", "nadie@example.com")]

    assert first == [204, 204]
    assert [response.status_code for response in again] == [429, 429]
    assert again[0].json() == again[1].json() == {"detail": "Demasiados intentos. Probá de nuevo en 60 segundos."}
    assert later == [204, 204]
    assert len(mail.outbox) == 2


def test_after_signing_up_resend_waits_its_60_seconds(free_plan_row):
    _register()

    response = _resend("ana@example.com")

    assert response.status_code == 429
    assert len(mail.outbox) == 1


def test_one_ip_cannot_send_emails_to_many_addresses():
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"auth_email": "3/hour"}):
        api = APIClient(REMOTE_ADDR="10.1.1.1")
        answers = [_resend(f"nadie{n}@example.com", api).status_code for n in range(2)]
        # Shared with password reset.
        answers.append(api.post("/api/v1/auth/password-reset/", {"email": "otro@example.com"}, format="json").status_code)
        answers.append(_resend("ultimo@example.com", api).status_code)

    assert answers == [204, 204, 204, 429]


def test_with_redis_down_resend_is_still_limited():
    with override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [_resend("nadie@example.com").status_code for _ in range(2)]

    assert answers == [204, 429]


def test_a_queue_that_does_not_answer_is_the_same_502_for_every_email(user):
    with mock.patch.object(tasks.send_account_email, "delay", side_effect=BrokerError("down")):
        answers = [_resend(email) for email in ("tester@test.com", "nadie@test.com")]

    assert [response.status_code for response in answers] == [502, 502]
    assert answers[0].json() == answers[1].json() == {"detail": "No pudimos mandar el email. Probá de nuevo en unos minutos."}


def test_a_failed_send_in_the_worker_is_logged_without_the_email(caplog):
    account = _unverified()
    with mock.patch("apps.users.email_verification.send_mail", side_effect=smtplib.SMTPServerDisconnected("caído")), \
            caplog.at_level("INFO", logger="apps.users.tasks"):
        response = _resend(account.email)

    assert response.status_code == 204
    assert [record.getMessage() for record in caplog.records if record.name == "apps.users.tasks"] == [
        f"account_email.send_failed kind=resend_verification user_id={account.pk} error=SMTPServerDisconnected",
    ]


def logged(caplog):
    """Every record as production writes it (JsonFormatter: message and extras, Celery's `data` included)."""
    return "\n".join(JsonFormatter().format(record) for record in caplog.records)


def test_nothing_logs_the_email_or_the_link(free_plan_row, caplog):
    _unverified("pendiente@example.com")
    with caplog.at_level("DEBUG"):
        _register()
        token = _signup_token(mail.outbox[0])[0]["token"][0]
        APIClient().post(VERIFY_URL, {"token": token}, format="json")
        _resend("ana@example.com")
        _resend("nadie@example.com")
        _resend("pendiente@example.com")

    text = logged(caplog)
    # The worker's lines are there (the task ran), without the address.
    assert "send_account_email" in text
    assert "account_email.sent kind=resend_verification" in text
    for secret in ("ana@example.com", "nadie@example.com", "pendiente@example.com", token):
        assert secret not in text
    resent = _signup_token(mail.outbox[-1])[0]["token"][0]
    assert resent not in text
