"""Password reset (A4): the link by email, and the new password that ends every session."""
import threading
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.core.structured_logging import JsonFormatter
from apps.users import views_auth
from apps.users.email_verification import SIGNUP, make_password_reset_token, make_token, password_reset_tokens
from apps.users.models import User
from apps.users.tests.test_login_throttle import REDIS_DOWN, _refused
from apps.users.throttles import EmailAttemptThrottle

pytestmark = pytest.mark.django_db

RESET_URL = "/api/v1/auth/password-reset/"
CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
VERIFY_URL = "/api/v1/auth/verify-email/"
LOGIN_URL = "/api/token/"
REFRESH_URL = "/api/token/refresh/"
ME_URL = "/api/v1/me/"
OLD_PASSWORD = "password"
NEW_PASSWORD = "contraseña-nueva-123"
INVALID_LINK = {"token": ["El link no es válido o ya venció."]}


def _ask(email="tester@test.com", api=None):
    return (api or APIClient()).post(RESET_URL, {"email": email}, format="json")


def _link(message):
    link = next(word for word in message.body.split() if "/recuperar#" in word)
    return link, parse_qs(urlparse(link).fragment)


def _token_by_email(email="tester@test.com"):
    assert _ask(email).status_code == 204
    _, query = _link(mail.outbox[-1])
    return query["token"][0]


def _confirm(token, password=NEW_PASSWORD, api=None):
    return (api or APIClient()).post(CONFIRM_URL, {"token": token, "password": password}, format="json")


def _login(email="tester@test.com", password=OLD_PASSWORD):
    return APIClient().post(LOGIN_URL, {"email": email, "password": password}, format="json")


def _client(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api


def test_the_link_goes_to_the_account_email_with_its_token_and_email(user):
    response = _ask("TESTER@ｔｅｓｔ.com")

    assert response.status_code == 204
    assert response.content == b""
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["tester@test.com"]
    link, query = _link(message)
    # In the fragment: a browser never sends it, so it stays out of the web host's access log.
    assert link.startswith("http://localhost:3000/recuperar#")
    assert urlparse(link).query == ""
    # The contract's link: the email lets A4 say whose account it is and sign in after saving.
    assert set(query) == {"token", "email"}
    assert query["email"] == ["tester@test.com"]
    assert "vence en 24 horas" in message.body


def test_an_email_without_an_account_gets_the_same_answer_and_no_email(user):
    known = _ask("tester@test.com")
    unknown = _ask("nadie@test.com")

    assert (known.status_code, known.content) == (unknown.status_code, unknown.content) == (204, b"")
    assert [message.to for message in mail.outbox] == [["tester@test.com"]]


def test_the_new_password_ends_every_session_and_answers_no_tokens(user):
    devices = [_login().json() for _ in range(2)]
    token = _token_by_email()

    response = _confirm(token)

    assert response.status_code == 204
    assert response.content == b""
    for tokens in devices:
        # Like POST /me/password/: the tokens of the old password stop at once.
        assert _client(tokens["access"]).get(ME_URL).status_code == 401
        assert APIClient().post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 401
    # "Guardar y entrar": the web app signs in with the email of the link and the new password.
    assert _login(password=NEW_PASSWORD).status_code == 200
    assert _login(password=OLD_PASSWORD).status_code == 401
    # The link reached the account's inbox: the email is verified too.
    user.refresh_from_db()
    assert user.is_email_verified


def test_the_link_works_once(user):
    token = _token_by_email()

    assert _confirm(token).status_code == 204
    again = _confirm(token, password="otra-clave-Nueva-99")

    assert again.status_code == 400
    assert again.json() == INVALID_LINK
    assert _login(password=NEW_PASSWORD).status_code == 200


def test_any_other_password_change_cancels_the_link(user):
    token = _token_by_email()
    access = _login().json()["access"]
    changed = _client(access).post(
        "/api/v1/me/password/", {"current_password": OLD_PASSWORD, "new_password": "otra-clave-Nueva-99"}, format="json",
    )
    assert changed.status_code == 200

    assert _confirm(token).json() == INVALID_LINK


def test_a_newer_link_does_not_cancel_the_older_one_until_one_is_used(user):
    first = _token_by_email()
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=time.time() + 61):
        second = _token_by_email()

    assert _confirm(second).status_code == 204
    assert _confirm(first).json() == INVALID_LINK


def test_an_expired_link_is_refused(user):
    token = _token_by_email()
    later = password_reset_tokens._now() + timedelta(hours=24, seconds=1)

    with mock.patch.object(password_reset_tokens, "_now", return_value=later):
        response = _confirm(token)

    assert response.json() == INVALID_LINK
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


@pytest.mark.parametrize("mangle", [
    lambda token: token[:-1] + ("0" if token[-1] != "0" else "1"),
    lambda token: token.split(".")[1],
    lambda token: "00000000000000000000000000000000." + token.split(".")[1],
    lambda token: "no-es-un-token",
    lambda token: "zz." + token.split(".")[1],
])
def test_an_altered_link_is_refused(user, mangle):
    token = _token_by_email()

    assert _confirm(mangle(token)).json() == INVALID_LINK


def test_a_token_of_djangos_own_generator_is_not_a_reset_token(user):
    """The salt of PasswordResetTokens: a token Django's admin flow (or any other use) makes does not pass."""
    from django.contrib.auth.tokens import default_token_generator

    forged = f"{user.pk.hex}.{default_token_generator.make_token(user)}"

    assert _confirm(forged).json() == INVALID_LINK
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


def test_signing_in_does_not_cancel_the_link(user):
    """The generator hashes last_login, but the JWT login never writes it."""
    token = _token_by_email()
    assert _login().status_code == 200
    user.refresh_from_db()
    assert user.last_login is None

    assert _confirm(token).status_code == 204


def test_the_new_password_drops_what_the_old_one_left_behind(user):
    """Whoever had the password before (a squatter of the email) leaves a change of email and the sign-up's nonce."""
    User.objects.filter(pk=user.pk).update(pending_email="ocupante@evil.com", signup_nonce_hash="x" * 64)
    token = _token_by_email()

    assert _confirm(token).status_code == 204

    user.refresh_from_db()
    assert (user.pending_email, user.signup_nonce_hash, user.is_email_verified) == (None, None, True)


def test_other_tokens_of_the_project_are_not_reset_tokens(user):
    signup = make_token(user, SIGNUP)
    assert _confirm(signup).json() == INVALID_LINK
    # Nor is a reset token a verification link.
    reset = _token_by_email()
    assert APIClient().post(VERIFY_URL, {"token": reset}, format="json").status_code == 400


def test_a_weak_password_is_refused_in_spanish_and_the_link_keeps_working(user):
    token = _token_by_email()

    weak = _confirm(token, password="12345678")
    similar = _confirm(token, password="tester@test")

    assert weak.status_code == 400
    assert weak.json() == {"password": ["Esa contraseña es muy común. Elegí otra.", "La contraseña no puede ser solo números."]}
    assert similar.json() == {"password": ["La contraseña se parece demasiado a tu nombre o a tu email."]}
    assert _confirm(token).status_code == 204


def test_the_link_is_checked_before_the_password(user):
    response = _confirm("no-es-un-token", password="1")

    assert response.json() == INVALID_LINK


def test_an_account_without_a_password_creates_one(user):
    user.set_unusable_password()
    user.save()
    token = _token_by_email()

    assert _confirm(token).status_code == 204
    assert _login(password=NEW_PASSWORD).status_code == 200


def test_a_deactivated_account_gets_no_email_and_its_link_does_nothing(user):
    token = _token_by_email()
    User.objects.filter(pk=user.pk).update(is_active=False)

    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=time.time() + 61):
        assert _ask().status_code == 204
    assert len(mail.outbox) == 1
    assert _confirm(token).json() == INVALID_LINK


def test_asking_is_once_every_60_seconds_per_email_with_or_without_an_account(user):
    start = time.time()
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=start):
        first = [_ask(email).status_code for email in ("tester@test.com", "nadie@test.com")]
        again = [_ask(email) for email in ("Tester@TEST.com", "NADIE@test.com")]
    with mock.patch.object(EmailAttemptThrottle, "timer", return_value=start + 61):
        later = [_ask(email).status_code for email in ("tester@test.com", "nadie@test.com")]

    assert first == later == [204, 204]
    assert [response.status_code for response in again] == [429, 429]
    assert again[0].json() == again[1].json()
    assert len(mail.outbox) == 2


def test_with_redis_down_asking_and_confirming_are_still_limited(user):
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"password_reset_confirm": "3/hour"}), \
            override_settings(CACHES=REDIS_DOWN), _refused():
        asked = [_ask("nadie@test.com").status_code for _ in range(2)]
        confirmed = [_confirm("no-es-un-token").status_code for _ in range(4)]

    assert asked == [204, 429]
    assert confirmed == [400, 400, 400, 429]


def test_confirming_is_limited_per_ip(user):
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"password_reset_confirm": "2/hour"}):
        answers = [_confirm("no-es-un-token", api=APIClient(REMOTE_ADDR="10.2.0.1")).status_code for _ in range(3)]
        elsewhere = _confirm("no-es-un-token", api=APIClient(REMOTE_ADDR="10.2.0.2"))

    assert answers == [400, 400, 429]
    assert elsewhere.status_code == 400


def test_nothing_logs_the_email_or_the_token(user, caplog):
    with caplog.at_level("DEBUG"):
        token = _token_by_email()
        _ask("nadie@test.com")
        _confirm(token)

    # As production writes them (JsonFormatter: message and extras, Celery's `data` included).
    text = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "account_email.sent kind=password_reset" in text
    assert "password_reset.done" in text
    for secret in ("tester@test.com", "nadie@test.com", token, token.split(".")[1]):
        assert secret not in text


def test_the_reset_link_lasts_what_the_setting_says(user, settings):
    settings.PASSWORD_RESET_TIMEOUT = 3600
    token = _token_by_email()

    assert "vence en 1 horas" in mail.outbox[0].body
    later = timezone.now() + timedelta(hours=1, seconds=5)
    with mock.patch.object(password_reset_tokens, "_now", return_value=later.replace(tzinfo=None)):
        assert _confirm(token).json() == INVALID_LINK


class ConcurrentConfirmTests(TransactionTestCase):
    """Two requests with the same link at once: the row lock lets only one of them set a password."""

    def setUp(self):
        self.user = User.objects.create_user(username="carrera", email="carrera@test.com", password=OLD_PASSWORD)

    def test_exactly_one_of_two_confirms_with_the_same_link_wins(self):
        token = make_password_reset_token(self.user)
        both_checked = threading.Barrier(2)
        real_check = views_auth.check_new_password

        def slow_check(value, user):
            # Past the token check, each one waits for the other. With the lock the second one is
            # still waiting for the row, so the first gives up after a moment and saves; without it
            # both have passed the check with the old password and both would save.
            try:
                both_checked.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass
            return real_check(value, user)

        answers = []

        def attempt(password):
            try:
                answers.append(_confirm(token, password=password).status_code)
            finally:
                connection.close()

        with mock.patch.object(views_auth, "check_new_password", side_effect=slow_check):
            threads = [threading.Thread(target=attempt, args=(password,)) for password in ("primera-Clave-2026", "segunda-Clave-2026")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        assert sorted(answers) == [204, 400]
        self.user.refresh_from_db()
        assert self.user.check_password("primera-Clave-2026") != self.user.check_password("segunda-Clave-2026")
