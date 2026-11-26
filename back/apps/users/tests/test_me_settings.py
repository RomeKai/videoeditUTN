import pytest
from rest_framework.test import APIClient

from apps.users.models import User

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me/"
PASSWORD_URL = "/api/v1/me/password/"
PREFERENCES_URL = "/api/v1/me/notification-preferences/"
PASSWORD = "password"
NEW_PASSWORD = "contraseña-nueva-123"


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def test_patch_me_updates_name_and_splits_it_into_first_and_last(client, user):
    response = client.patch(ME_URL, {"name": "Ana Pérez García"}, format="json")

    user.refresh_from_db()
    assert response.status_code == 200
    assert (user.first_name, user.last_name) == ("Ana", "Pérez García")
    assert response.json()["name"] == "Ana Pérez García"


def test_patch_me_marks_the_tour_as_completed(client, user):
    response = client.patch(ME_URL, {"tour_completed": True}, format="json")

    user.refresh_from_db()
    assert response.status_code == 200
    assert user.tour_completed is True
    assert response.json()["tour_completed"] is True


def test_patch_me_rejects_an_empty_name(client, user):
    response = client.patch(ME_URL, {"name": ""}, format="json")

    user.refresh_from_db()
    assert response.status_code == 400
    assert "name" in response.json()
    assert user.first_name == ""


def test_password_change_sets_the_new_password(client, user):
    response = client.post(
        PASSWORD_URL, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, format="json",
    )

    user.refresh_from_db()
    assert response.status_code == 200
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)


def test_password_change_rejects_a_wrong_current_password(client, user):
    response = client.post(
        PASSWORD_URL, {"current_password": "otra", "new_password": NEW_PASSWORD}, format="json",
    )

    user.refresh_from_db()
    assert response.status_code == 400
    assert "current_password" in response.json()
    assert user.check_password(PASSWORD)


def test_password_change_rejects_a_weak_new_password(client, user):
    response = client.post(
        PASSWORD_URL, {"current_password": PASSWORD, "new_password": "corta"}, format="json",
    )

    user.refresh_from_db()
    assert response.status_code == 400
    assert "new_password" in response.json()
    assert user.check_password(PASSWORD)


def test_notification_preferences_default_to_all_on(client):
    response = client.get(PREFERENCES_URL)

    assert response.status_code == 200
    assert response.json() == {
        "project_ready": True,
        "render_finished": True,
        "project_failed": True,
        "post_failed": True,
        "low_balance": True,
    }


def test_notification_preferences_patch_persists_only_the_sent_switches(client, user):
    response = client.patch(PREFERENCES_URL, {"low_balance": False}, format="json")

    assert response.status_code == 200
    assert response.json()["low_balance"] is False
    assert response.json()["project_ready"] is True
    assert client.get(PREFERENCES_URL).json()["low_balance"] is False


def test_notification_preferences_are_per_user(client, user):
    client.patch(PREFERENCES_URL, {"post_failed": False}, format="json")
    other = User.objects.create_user(username="otro", email="otro@test.com", password="password")
    other_client = APIClient()
    other_client.force_authenticate(user=other)

    assert other_client.get(PREFERENCES_URL).json()["post_failed"] is True


@pytest.mark.parametrize("url", [ME_URL, PASSWORD_URL, PREFERENCES_URL])
def test_user_endpoints_require_authentication(url):
    response = APIClient().generic("PATCH" if url != PASSWORD_URL else "POST", url)

    assert response.status_code == 401
