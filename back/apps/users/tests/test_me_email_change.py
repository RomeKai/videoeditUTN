"""PATCH /me with a new email, its verification link, and the other profile fields it stores."""
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.users.models import User, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me/"
VERIFY_URL = "/api/v1/auth/verify-email/"
PASSWORD = "password"
NEW_EMAIL = "nuevo@test.com"


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _other_user(email="otro@test.com"):
    return User.objects.create_user(username=email.split("@")[0], email=email, password=PASSWORD)


def _link_token(message):
    link = next(word for word in message.body.split() if "/verificar?" in word)
    query = parse_qs(urlparse(link).query)
    return query["token"][0], query["siguiente"][0], link


def _verify(token):
    return APIClient().post(VERIFY_URL, {"token": token}, format="json")


def test_a_new_email_waits_for_verification_and_gets_a_link(client, user):
    response = client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "tester@test.com"
    assert body["pending_email"] == NEW_EMAIL
    user.refresh_from_db()
    assert (user.email, user.pending_email) == ("tester@test.com", NEW_EMAIL)
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [NEW_EMAIL]
    token, next_page, link = _link_token(message)
    assert link.startswith("http://localhost:3000/verificar?")
    assert next_page == "/perfil"
    assert token
    assert "vence en 24 horas" in message.body


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"email": NEW_EMAIL}, "Escribí tu contraseña para cambiar el email."),
        ({"email": NEW_EMAIL, "current_password": "otra"}, "La contraseña actual no es correcta."),
    ],
)
def test_a_new_email_needs_the_right_password(client, user, payload, message):
    response = client.patch(ME_URL, payload, format="json")

    assert response.status_code == 400
    assert response.json() == {"current_password": [message]}
    user.refresh_from_db()
    assert user.pending_email is None
    assert mail.outbox == []


def test_an_email_of_another_account_is_rejected(client, user):
    _other_user("Ocupado@Test.com")

    response = client.patch(ME_URL, {"email": "ocupado@test.com", "current_password": PASSWORD}, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": ["Ya existe una cuenta con este email."]}
    user.refresh_from_db()
    assert user.pending_email is None
    assert mail.outbox == []


def test_a_wrong_password_does_not_tell_whether_the_email_is_taken(client):
    _other_user("ocupado@test.com")

    response = client.patch(ME_URL, {"email": "ocupado@test.com", "current_password": "otra"}, format="json")

    assert response.status_code == 400
    assert list(response.json()) == ["current_password"]


def test_the_current_email_changes_nothing(client, user):
    response = client.patch(ME_URL, {"email": "TESTER@test.com"}, format="json")

    assert response.status_code == 200
    assert response.json()["pending_email"] is None
    assert mail.outbox == []


def test_an_account_without_password_cannot_change_the_email_with_one(client, user):
    user.set_unusable_password()
    user.save(update_fields=["password"])

    response = client.patch(ME_URL, {"email": NEW_EMAIL}, format="json")

    assert response.status_code == 400
    assert "red social" in response.json()["current_password"][0]


def test_nothing_is_saved_when_one_field_fails(client, user):
    response = client.patch(
        ME_URL, {"name": "Ana Pérez", "email": NEW_EMAIL, "current_password": "otra"}, format="json",
    )

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.first_name == ""


def test_the_link_moves_the_pending_email_and_signs_in(client, user):
    client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")
    token, _, _ = _link_token(mail.outbox[0])

    response = _verify(token)

    assert response.status_code == 200
    tokens = response.json()
    assert set(tokens) == {"access", "refresh"}
    user.refresh_from_db()
    assert (user.email, user.pending_email, user.is_email_verified) == (NEW_EMAIL, None, True)
    signed_in = APIClient()
    signed_in.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = signed_in.get(ME_URL).json()
    assert (me["email"], me["pending_email"]) == (NEW_EMAIL, None)
    # The new email signs in.
    login = APIClient().post("/api/token/", {"email": NEW_EMAIL, "password": PASSWORD}, format="json")
    assert login.status_code == 200


def test_a_link_works_once(client):
    client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")
    token, _, _ = _link_token(mail.outbox[0])

    assert _verify(token).status_code == 200
    again = _verify(token)

    assert again.status_code == 400
    assert again.json() == {"token": ["El link no es válido o ya venció."]}


def test_a_newer_change_cancels_the_older_link(client, user):
    client.patch(ME_URL, {"email": "primero@test.com", "current_password": PASSWORD}, format="json")
    client.patch(ME_URL, {"email": "segundo@test.com", "current_password": PASSWORD}, format="json")
    first, _, _ = _link_token(mail.outbox[0])
    second, _, _ = _link_token(mail.outbox[1])

    assert _verify(first).status_code == 400
    assert _verify(second).status_code == 200
    user.refresh_from_db()
    assert user.email == "segundo@test.com"


def test_an_expired_or_altered_link_is_rejected(client, user):
    client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")
    token, _, _ = _link_token(mail.outbox[0])

    with mock.patch("django.core.signing.time.time", return_value=time.time() + 25 * 3600):
        expired = _verify(token)
    altered = _verify(token[:-2] + ("aa" if not token.endswith("aa") else "bb"))

    assert expired.status_code == 400
    assert altered.status_code == 400
    user.refresh_from_db()
    assert (user.email, user.pending_email) == ("tester@test.com", NEW_EMAIL)


def test_an_email_taken_after_the_link_was_sent_is_not_applied(client, user):
    client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")
    token, _, _ = _link_token(mail.outbox[0])
    _other_user(NEW_EMAIL)

    response = _verify(token)

    assert response.status_code == 400
    assert response.json() == {"token": ["Ya existe una cuenta con este email."]}
    user.refresh_from_db()
    assert user.email == "tester@test.com"


def test_email_changes_are_throttled_but_other_edits_are_not(client, user):
    attempts = [
        client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": "otra"}, format="json").status_code
        for _ in range(5)
    ]
    blocked = client.patch(ME_URL, {"email": NEW_EMAIL, "current_password": PASSWORD}, format="json")
    edits = [client.patch(ME_URL, {"tour_completed": True}, format="json").status_code for _ in range(10)]

    assert attempts == [400] * 5
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos.")
    assert edits == [200] * 10
    assert client.get(ME_URL).status_code == 200
    assert mail.outbox == []


def test_the_welcome_use_case_is_stored_and_returned(client, user):
    response = client.patch(ME_URL, {"onboarding_use_case": "gaming"}, format="json")

    assert response.status_code == 200
    assert response.json()["onboarding_use_case"] == "gaming"
    user.refresh_from_db()
    assert user.onboarding_use_case == "gaming"
    assert client.get(ME_URL).json()["onboarding_use_case"] == "gaming"
    assert client.patch(ME_URL, {"onboarding_use_case": "cocina"}, format="json").status_code == 400


def test_the_chosen_workspace_is_stored(client, user, workspace):
    agency = Workspace.objects.create(name="Agencia", owner=_other_user())
    WorkspaceMember.objects.create(
        workspace=agency, user=user, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE,
    )

    response = client.patch(ME_URL, {"active_workspace_id": str(agency.pk)}, format="json")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.last_workspace_id == agency.pk


@pytest.mark.parametrize("status", [None, WorkspaceMember.Status.PENDING])
def test_a_workspace_without_access_is_rejected(client, user, status):
    foreign = Workspace.objects.create(name="Ajeno", owner=_other_user())
    if status:
        WorkspaceMember.objects.create(workspace=foreign, user=user, role=WorkspaceMember.Role.EDITOR, status=status)

    response = client.patch(ME_URL, {"active_workspace_id": str(foreign.pk)}, format="json")

    assert response.status_code == 400
    assert response.json() == {"active_workspace_id": ["No sos miembro de ese workspace."]}
    user.refresh_from_db()
    assert user.last_workspace_id is None
