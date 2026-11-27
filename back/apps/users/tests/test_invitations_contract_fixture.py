"""
The invitation bodies of front/src/api/contracts/drf/responses.json are the
ones this backend gives: front/src/api/contracts/drf-invitaciones.test.ts
parses them with the contract (zod) and with the web app's error helpers.
This test builds each of them with the real endpoints and compares every
value (like test_auth_contract_fixture.py). Ids, dates and tokens change on
each run: they are compared by shape and then taken from the fixture.

When an answer changes on purpose, copy the new bodies into the file (only
the keys of KEYS and ERROR_KEYS). The failure prints them, and so does
``PRINT_DRF_FIXTURE=1 python -m pytest apps/users/tests/test_invitations_contract_fixture.py -s``.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan
from apps.users.models import User, WorkspaceMember

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[4] / "front" / "src" / "api" / "contracts" / "drf" / "responses.json"

KEYS = ["invitation_invited", "invitation_preview", "invitation_accepted_new_account", "invitation_accepted_session"]
ERROR_KEYS = [
    "invite_400_already_member",
    "invite_400_invalid_email",
    "invite_400_role",
    "invite_403_seats",
    "invite_403_role",
    "invitation_resend_404",
    "invitation_404",
    "invitation_accept_400_weak",
    "invitation_accept_401_has_account",
    "invitation_accept_403_other_account",
    "invitation_accept_410",
]
NEW_ACCOUNT = {"name": "Juana Pérez", "password": "una-clave-Nueva-2026", "terms_accepted": True}


def _client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _error(response):
    return {"status": response.status_code, "body": response.json()}


def _token():
    link = next(word for word in mail.outbox[-1].body.split() if "/invitacion#" in word)
    return parse_qs(urlparse(link).fragment)["token"][0]


def _real_bodies():
    SubscriptionPlan.objects.get_or_create(name=settings.DEFAULT_PLAN_NAME)
    owner = User.objects.create_user(
        username="duena", email="duena@test.com", password="password", first_name="Dana", last_name="Dueña",
    )
    workspace = owner.owned_workspaces.get()
    workspace.name = "Estudio Norte"
    workspace.subscription_plan = SubscriptionPlan.objects.create(name="Equipo", max_members=4)
    workspace.save(update_fields=["name", "subscription_plan"])
    editor = User.objects.create_user(username="editora", email="editora@test.com", password="password")
    WorkspaceMember.objects.create(
        workspace=workspace, user=editor, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE,
    )
    url = f"/api/v1/workspaces/{workspace.pk}/invitations/"
    api = _client(owner)

    invited = api.post(url, {"emails": ["juana@example.com"], "role": "editor"}, format="json")
    assert invited.status_code == 201
    token = _token()
    preview = APIClient().post("/api/v1/invitations/preview/", {"token": token}, format="json")
    already = api.post(url, {"emails": ["JUANA@example.com"], "role": "editor"}, format="json")
    invalid = api.post(url, {"emails": ["juana"], "role": "editor"}, format="json")
    role = api.post(url, {"emails": ["otra@example.com"], "role": "owner"}, format="json")
    denied = _client(editor).post(url, {"emails": ["otra@example.com"], "role": "editor"}, format="json")
    seats = api.post(url, {"emails": ["dos@example.com", "tres@example.com"], "role": "editor"}, format="json")
    weak = APIClient().post("/api/v1/invitations/accept/", {**NEW_ACCOUNT, "password": "12345678", "token": token}, format="json")
    other = _client(editor).post("/api/v1/invitations/accept/", {"token": token}, format="json")
    accepted = APIClient().post("/api/v1/invitations/accept/", {**NEW_ACCOUNT, "token": token}, format="json")
    assert accepted.status_code == 200
    gone = APIClient().post("/api/v1/invitations/accept/", {**NEW_ACCOUNT, "token": token}, format="json")
    missing = APIClient().post("/api/v1/invitations/preview/", {"token": token}, format="json")
    resend = api.post(f"{url}{invited.json()['invited'][0]['id']}/resend/")

    # An email that already has an account: signed out it must sign in; signed in it accepts.
    sofia = User.objects.create_user(username="sofia", email="sofia@example.com", password="password")
    assert api.post(url, {"emails": ["sofia@example.com"], "role": "viewer"}, format="json").status_code == 201
    second = _token()
    has_account = APIClient().post("/api/v1/invitations/accept/", {**NEW_ACCOUNT, "token": second}, format="json")
    with_session = _client(sofia).post("/api/v1/invitations/accept/", {"token": second}, format="json")
    return {
        "invitation_invited": invited.json(),
        "invitation_preview": preview.json(),
        "invitation_accepted_new_account": accepted.json(),
        "invitation_accepted_session": with_session.json(),
        "errors": {
            "invite_400_already_member": _error(already),
            "invite_400_invalid_email": _error(invalid),
            "invite_400_role": _error(role),
            "invite_403_seats": _error(seats),
            "invite_403_role": _error(denied),
            "invitation_resend_404": _error(resend),
            "invitation_404": _error(missing),
            "invitation_accept_400_weak": _error(weak),
            "invitation_accept_401_has_account": _error(has_account),
            "invitation_accept_403_other_account": _error(other),
            "invitation_accept_410": _error(gone),
        },
    }


def _same_shape(real, fixture):
    """The values that are new on each run: checked by shape in both, then the fixture's."""
    for bodies in (real, fixture):
        [row] = bodies["invitation_invited"]["invited"]
        uuid.UUID(row["id"])
        uuid.UUID(row["invited_by"]["id"])
        datetime.fromisoformat(row["joined_at"].replace("Z", "+00:00"))
        uuid.UUID(bodies["invitation_preview"]["workspace_id"])
        uuid.UUID(bodies["invitation_accepted_new_account"]["workspace_id"])
        uuid.UUID(bodies["invitation_accepted_session"]["workspace_id"])
        tokens = bodies["invitation_accepted_new_account"]["tokens"]
        assert set(tokens) == {"access", "refresh"} and all(value.count(".") == 2 for value in tokens.values())
    row, theirs = real["invitation_invited"]["invited"][0], fixture["invitation_invited"]["invited"][0]
    row["id"], row["joined_at"], row["invited_by"]["id"] = theirs["id"], theirs["joined_at"], theirs["invited_by"]["id"]
    for key in ("invitation_preview", "invitation_accepted_new_account", "invitation_accepted_session"):
        real[key]["workspace_id"] = fixture[key]["workspace_id"]
    real["invitation_accepted_new_account"]["tokens"] = fixture["invitation_accepted_new_account"]["tokens"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="Sin el frontend al lado (front/): nada que comparar.")
def test_the_invitation_bodies_of_the_contract_fixture_are_the_real_ones():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real = _real_bodies()
    if os.environ.get("PRINT_DRF_FIXTURE"):
        print("FIXTURE-JSON:" + json.dumps(real, ensure_ascii=False))

    found = {
        **{key: fixture.get(key) for key in KEYS},
        "errors": {key: fixture.get("errors", {}).get(key) for key in ERROR_KEYS},
    }
    assert all(found[key] for key in KEYS), "Faltan en responses.json:\n" + json.dumps(real, ensure_ascii=False, indent=2)
    _same_shape(real, found)
    assert found == real, "Actualizá responses.json con:\n" + json.dumps(real, ensure_ascii=False, indent=2)
