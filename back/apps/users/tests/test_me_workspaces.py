"""/me: the active workspace (last chosen, never from the header), the order of the list and the first steps."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan
from apps.users.models import User, Workspace, WorkspaceMember
from apps.videos.models import BrandKit, VideoProject

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me/"


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _other_user(email="otro@test.com"):
    return User.objects.create_user(username=email.split("@")[0], email=email, password="password")


def _member_of(user, name, created_days_ago=0, status=WorkspaceMember.Status.ACTIVE, role=WorkspaceMember.Role.EDITOR):
    workspace = Workspace.objects.create(name=name, owner=_other_user(f"{name.lower().replace(' ', '')}@test.com"))
    if created_days_ago:
        Workspace.objects.filter(pk=workspace.pk).update(created_at=timezone.now() - timedelta(days=created_days_ago))
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role, status=status)
    return workspace


def test_the_chosen_workspace_comes_back_as_the_active_one(client, workspace, user):
    agency = _member_of(user, "Agencia")

    client.patch(ME_URL, {"active_workspace_id": str(agency.pk)}, format="json")

    assert client.get(ME_URL).json()["active_workspace_id"] == str(agency.pk)
    # The header of a request is the browser's choice for that request, not the stored one.
    with_header = client.get(ME_URL, HTTP_X_WORKSPACE_ID=str(workspace.pk)).json()
    assert with_header["active_workspace_id"] == str(agency.pk)


# A fixed id that matches no workspace: a random one changes the test id on every
# collection, and parallel runs (pytest -n) refuse to start.
@pytest.mark.parametrize("header", ["no-es-un-uuid", "00000000-0000-4000-8000-000000000000", "foreign"])
def test_a_stale_header_never_breaks_me(client, workspace, header):
    if header == "foreign":
        header = str(Workspace.objects.create(name="Ajeno", owner=_other_user()).pk)

    response = client.get(ME_URL, HTTP_X_WORKSPACE_ID=header)

    assert response.status_code == 200
    assert response.json()["active_workspace_id"] == str(workspace.pk)


@pytest.mark.parametrize("lose_access", ["pending", "removed", "deleted"])
def test_a_workspace_the_user_lost_falls_back_to_the_default(client, user, workspace, lose_access):
    agency = _member_of(user, "Agencia")
    client.patch(ME_URL, {"active_workspace_id": str(agency.pk)}, format="json")
    membership = WorkspaceMember.objects.get(workspace=agency, user=user)
    if lose_access == "pending":
        WorkspaceMember.objects.filter(pk=membership.pk).update(status=WorkspaceMember.Status.PENDING)
    elif lose_access == "removed":
        membership.delete()
    else:
        agency.delete()

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["active_workspace_id"] == str(workspace.pk)


def test_without_an_own_workspace_the_default_is_the_oldest_membership(user, workspace):
    workspace.delete()
    newer = _member_of(user, "Nueva")
    older = _member_of(user, "Vieja")
    WorkspaceMember.objects.filter(workspace=older, user=user).update(joined_at=timezone.now() - timedelta(days=3))
    api = APIClient()
    api.force_authenticate(user=user)

    body = api.get(ME_URL).json()

    assert body["active_workspace_id"] == str(older.pk)
    assert {item["id"] for item in body["workspaces"]} == {str(newer.pk), str(older.pk)}


def test_a_user_without_workspaces_gets_an_empty_session(client, workspace):
    workspace.delete()

    body = client.get(ME_URL).json()

    assert body["active_workspace_id"] is None
    assert body["workspaces"] == []
    assert body["onboarding"] == {
        "has_project": False, "has_brand_kit": False, "has_social_account": False, "has_invited_member": False,
    }


def test_workspaces_are_listed_oldest_first(client, user, workspace):
    middle = _member_of(user, "Medio", created_days_ago=5)
    oldest = _member_of(user, "Viejo", created_days_ago=10)

    names = [item["name"] for item in client.get(ME_URL).json()["workspaces"]]

    assert names == [oldest.name, middle.name, workspace.name]


def test_the_number_of_queries_does_not_grow_with_the_workspaces(client, user):
    with CaptureQueriesContext(connection) as one:
        client.get(ME_URL)
    for n in range(4):
        _member_of(user, f"Equipo {n}")
    with CaptureQueriesContext(connection) as five:
        body = client.get(ME_URL).json()

    assert len(body["workspaces"]) == 5
    assert len(five) == len(one)


def test_first_steps_start_empty(client):
    assert client.get(ME_URL).json()["onboarding"] == {
        "has_project": False, "has_brand_kit": False, "has_social_account": False, "has_invited_member": False,
    }


def test_first_steps_come_from_the_active_workspace(client, user, workspace):
    VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="Primero", source_file="test/a.mp4")
    BrandKit.objects.create(workspace=workspace, name="Marca")
    team = SubscriptionPlan.objects.create(name="Equipo de prueba", max_members=5)
    Workspace.objects.filter(pk=workspace.pk).update(ayrshare_profile_key="perfil", subscription_plan=team)
    # A pending invitation already counts as having invited someone.
    WorkspaceMember.objects.create(
        workspace=workspace, user=_other_user(), role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.PENDING,
    )

    assert client.get(ME_URL).json()["onboarding"] == {
        "has_project": True, "has_brand_kit": True, "has_social_account": True, "has_invited_member": True,
    }

    # Another workspace has its own first steps.
    agency = _member_of(user, "Agencia")
    body = client.patch(ME_URL, {"active_workspace_id": str(agency.pk)}, format="json").json()
    # Its owner's own membership row is not an invitation; the user's is.
    assert body["onboarding"] == {
        "has_project": False, "has_brand_kit": False, "has_social_account": False, "has_invited_member": True,
    }


def test_the_owner_alone_has_not_invited_anyone(client, workspace):
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 1

    assert client.get(ME_URL).json()["onboarding"]["has_invited_member"] is False
