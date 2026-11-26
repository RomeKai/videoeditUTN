"""Changing the password ends the other sessions, keeps this one and limits the attempts."""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/token/"
REFRESH_URL = "/api/token/refresh/"
ME_URL = "/api/v1/me/"
PASSWORD_URL = "/api/v1/me/password/"
PASSWORD = "password"
NEW_PASSWORD = "contraseña-nueva-123"


def _login(email="tester@test.com", password=PASSWORD):
    response = APIClient().post(LOGIN_URL, {"email": email, "password": password}, format="json")
    assert response.status_code == 200, response.content
    return response.json()


def _client(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api


def _change(access, current=PASSWORD, new=NEW_PASSWORD):
    return _client(access).post(
        PASSWORD_URL, {"current_password": current, "new_password": new}, format="json",
    )


def test_password_change_ends_every_other_session(user):
    this_device = _login()
    other_device = _login()

    response = _change(this_device["access"])

    assert response.status_code == 200
    # Access tokens issued before the change stop working at once, on every device.
    assert _client(this_device["access"]).get(ME_URL).status_code == 401
    assert _client(other_device["access"]).get(ME_URL).status_code == 401
    # And their refresh tokens cannot start a new session.
    for old in (this_device, other_device):
        refreshed = APIClient().post(REFRESH_URL, {"refresh": old["refresh"]}, format="json")
        assert refreshed.status_code == 401


def test_password_change_keeps_this_client_signed_in_with_a_new_pair(user):
    tokens = _login()

    body = _change(tokens["access"]).json()

    assert set(body) == {"access", "refresh"}
    assert _client(body["access"]).get(ME_URL).status_code == 200
    refreshed = APIClient().post(REFRESH_URL, {"refresh": body["refresh"]}, format="json")
    assert refreshed.status_code == 200
    assert _client(refreshed.json()["access"]).get(ME_URL).status_code == 200
    # The new password signs in; the old one no longer does.
    _login(password=NEW_PASSWORD)
    assert APIClient().post(LOGIN_URL, {"email": "tester@test.com", "password": PASSWORD}, format="json").status_code == 401


def test_a_failed_change_keeps_the_sessions(user):
    tokens = _login()

    response = _change(tokens["access"], current="otra")

    assert response.status_code == 400
    assert _client(tokens["access"]).get(ME_URL).status_code == 200
    assert APIClient().post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 200


def test_guessing_the_current_password_is_throttled(user):
    api = APIClient()
    api.force_authenticate(user=user)
    payload = {"current_password": "otra", "new_password": NEW_PASSWORD}

    answers = [api.post(PASSWORD_URL, payload, format="json").status_code for _ in range(5)]
    blocked = api.post(PASSWORD_URL, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, format="json")

    assert answers == [400] * 5
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos. Probá de nuevo en ")
    assert int(blocked["Retry-After"]) > 0
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_weak_password_messages_come_in_spanish(user):
    api = APIClient()
    api.force_authenticate(user=user)

    numeric = api.post(PASSWORD_URL, {"current_password": PASSWORD, "new_password": "48151623420"}, format="json")
    short = api.post(PASSWORD_URL, {"current_password": PASSWORD, "new_password": "corta"}, format="json")

    assert numeric.status_code == 400
    assert numeric.json()["new_password"] == ["La contraseña no puede ser solo números."]
    assert short.json()["new_password"] == ["La contraseña tiene que tener 8 caracteres como mínimo."]


def test_an_account_without_password_gets_a_clear_error(user):
    # Social login: the account never had a password to confirm.
    user.set_unusable_password()
    user.save(update_fields=["password"])
    api = APIClient()
    api.force_authenticate(user=user)

    response = api.post(PASSWORD_URL, {"current_password": "algo", "new_password": NEW_PASSWORD}, format="json")

    assert response.status_code == 400
    assert "red social" in response.json()["current_password"][0]
    user.refresh_from_db()
    assert not user.has_usable_password()
