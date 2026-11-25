from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.payments.models import Wallet
from apps.users.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me/"


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def test_me_requires_authentication():
    response = APIClient().get(ME_URL)

    assert response.status_code == 401


def test_me_returns_profile_and_owned_workspace(client, user, workspace):
    Wallet.objects.filter(workspace=workspace).update(available_balance=Decimal("250.0000000000"))

    response = client.get(ME_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.pk)
    assert body["email"] == "tester@test.com"
    assert body["is_email_verified"] is False
    assert body["tour_completed"] is False
    assert body["terms_accepted_at"] is None
    assert body["active_workspace_id"] == str(workspace.pk)
    assert body["workspaces"] == [
        {
            "id": str(workspace.pk),
            "name": workspace.name,
            "role": "owner",
            "plan": None,
            "available_balance": "250.0000000000",
        }
    ]


def test_me_lists_active_membership_with_its_role(client, user, workspace):
    agency = Workspace.objects.create(name="Agencia Norte", owner=_other_user())
    WorkspaceMember.objects.create(
        workspace=agency, user=user, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE,
    )

    body = client.get(ME_URL).json()

    roles = {item["id"]: item["role"] for item in body["workspaces"]}
    assert roles == {str(workspace.pk): "owner", str(agency.pk): "editor"}


def test_me_leaves_out_pending_invitations(client, user):
    invited = Workspace.objects.create(name="Invitado", owner=_other_user())
    WorkspaceMember.objects.create(
        workspace=invited, user=user, role=WorkspaceMember.Role.VIEWER, status=WorkspaceMember.Status.PENDING,
    )

    body = client.get(ME_URL).json()

    assert str(invited.pk) not in {item["id"] for item in body["workspaces"]}


def _other_user():
    from apps.users.models import User

    return User.objects.create_user(username="otro", email="otro@test.com", password="password")
