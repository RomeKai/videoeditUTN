"""
/api/v1/workspaces/: the role is the one in the workspace of the PATH (never
the header's), non-members get 404, and the owner only changes by transfer.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.permissions import ADMINS, DENIED, OWNER_ONLY
from apps.core.workspace import NOT_FOUND_DETAIL
from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.users.models import User, Workspace, WorkspaceMember
from apps.videos.models import BrandKit, VideoProject

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/workspaces/"
ADMIN = WorkspaceMember.Role.ADMIN
EDITOR = WorkspaceMember.Role.EDITOR
VIEWER = WorkspaceMember.Role.VIEWER
ACTIVE = WorkspaceMember.Status.ACTIVE
PENDING = WorkspaceMember.Status.PENDING


def url(workspace, suffix=""):
    return f"{LIST_URL}{workspace.pk}/{suffix}"


def member_url(workspace, member):
    return url(workspace, f"members/{member.pk}/")


def client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def new_user(name):
    return User.objects.create_user(username=name, email=f"{name}@test.com", password="password", first_name=name.title())


@pytest.fixture
def plan():
    return SubscriptionPlan.objects.create(name="Equipo de prueba", max_members=5)


@pytest.fixture
def team(user, workspace, plan):
    """The owner's workspace with a 5-seat plan, an admin, an editor, a viewer and a pending invitation."""
    workspace.subscription_plan = plan
    workspace.save(update_fields=["subscription_plan"])

    class Team:
        pass

    team = Team()
    team.workspace = workspace
    team.owner = user
    team.rows = {"owner": WorkspaceMember.objects.get(workspace=workspace, user=user)}
    for name, role, status in (("admin", ADMIN, ACTIVE), ("editor", EDITOR, ACTIVE), ("viewer", VIEWER, ACTIVE), ("invitada", EDITOR, PENDING)):
        person = new_user(name)
        setattr(team, name, person)
        team.rows[name] = WorkspaceMember.objects.create(workspace=workspace, user=person, role=role, status=status)
    return team


# --- list, create, detail ---------------------------------------------------


def test_the_list_has_owned_and_active_workspaces_with_the_role_in_each(user, workspace):
    agency = Workspace.objects.create(name="Agencia", owner=new_user("jefa"))
    WorkspaceMember.objects.create(workspace=agency, user=user, role=VIEWER, status=ACTIVE)
    invited = Workspace.objects.create(name="Invitado", owner=new_user("otra"))
    WorkspaceMember.objects.create(workspace=invited, user=user, role=EDITOR, status=PENDING)

    response = client_for(user).get(LIST_URL)

    assert response.status_code == 200
    assert [(item["id"], item["role"]) for item in response.json()] == [
        (str(workspace.pk), "owner"), (str(agency.pk), "viewer"),
    ]
    assert set(response.json()[0]) == {"id", "name", "owner", "subscription_plan", "created_at", "role"}


def test_create_makes_the_user_owner_on_the_default_plan_with_an_empty_wallet(user, workspace, settings):
    free = SubscriptionPlan.objects.filter(name=settings.DEFAULT_PLAN_NAME).first()
    if free is None:
        free = SubscriptionPlan.objects.create(name=settings.DEFAULT_PLAN_NAME, max_members=2)

    response = client_for(user).post(LIST_URL, {"name": "  Estudio Norte  "}, format="json")

    assert response.status_code == 201
    body = response.json()
    created = Workspace.objects.get(pk=body["id"])
    assert body["name"] == created.name == "Estudio Norte"
    assert body["owner"] == str(user.pk) and body["role"] == "owner"
    assert body["subscription_plan"] == str(free.pk) == str(created.subscription_plan_id)
    wallet = Wallet.objects.get(workspace=created)
    assert (wallet.available_balance, wallet.reserved_balance) == (Decimal("0"), Decimal("0"))
    row = WorkspaceMember.objects.get(workspace=created, user=user)
    assert (row.role, row.status) == (ADMIN, ACTIVE)
    # The first workspace is untouched.
    assert Workspace.objects.filter(owner=user).count() == 2


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, None])
def test_create_and_rename_refuse_a_bad_name_in_spanish(user, workspace, name):
    api = client_for(user)

    created = api.post(LIST_URL, {"name": name}, format="json")
    renamed = api.patch(url(workspace), {"name": name}, format="json")

    for response in (created, renamed):
        assert response.status_code == 400
        assert set(response.json()) == {"name"}
        assert "espacio" in response.json()["name"][0] or "caracteres" in response.json()["name"][0]
    assert Workspace.objects.filter(owner=user).count() == 1


def test_detail_is_for_every_role_and_names_who_to_ask(team):
    response = client_for(team.viewer).get(url(team.workspace))

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "viewer"
    # The owner first, then the ACTIVE admins; no editor, viewer or pending row.
    assert body["admins"] == [
        {"id": str(team.owner.pk), "name": team.owner.email, "role": "owner"},
        {"id": str(team.admin.pk), "name": "Admin", "role": "admin"},
    ]


# --- 404 for whoever is not in the workspace of the path --------------------


REQUESTS = [
    ("get", "", None),
    ("patch", "", {"name": "Otro"}),
    ("delete", "", {"confirm_name": "x"}),
    ("post", "transfer/", {"new_owner_id": "00000000-0000-4000-8000-000000000000"}),
    ("get", "members/", None),
    ("patch", "members/{member}/", {"role": "viewer"}),
    ("delete", "members/{member}/", None),
]


@pytest.mark.parametrize("who", ["stranger", "invited"])
@pytest.mark.parametrize("method, suffix, body", REQUESTS)
def test_a_non_member_gets_404_even_with_their_own_workspace_in_the_header(team, who, method, suffix, body):
    """The stranger owns a workspace (owner role there) and sends it in the header: it gives nothing here."""
    outsider = new_user("ajena") if who == "stranger" else team.invitada
    own = outsider.owned_workspaces.get()
    target = suffix.format(member=team.rows["editor"].pk)

    response = getattr(client_for(outsider), method)(
        url(team.workspace, target), body, format="json", HTTP_X_WORKSPACE_ID=str(own.pk),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND_DETAIL}
    team.workspace.refresh_from_db()
    assert team.workspace.owner_id == team.owner.pk
    assert WorkspaceMember.objects.filter(workspace=team.workspace).count() == 5


@pytest.mark.parametrize("bad", ["no-es-un-uuid", "00000000-0000-4000-8000-000000000000"])
def test_a_malformed_or_unknown_id_is_the_same_json_404(user, workspace, bad):
    response = client_for(user).get(f"{LIST_URL}{bad}/")

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND_DETAIL}


def test_without_a_session_it_is_401(workspace):
    assert APIClient().get(url(workspace)).status_code == 401
    assert APIClient().get(LIST_URL).status_code == 401


# --- the role of the path's workspace decides --------------------------------

# (method, suffix, body, roles allowed, sentence of the 403)
ROLE_RULES = [
    ("patch", "", {"name": "Otro nombre"}, {"owner", "admin"}, DENIED[ADMINS]),
    ("delete", "", {"confirm_name": "no coincide"}, {"owner"}, DENIED[OWNER_ONLY]),
    ("post", "transfer/", {"new_owner_id": "00000000-0000-4000-8000-000000000000"}, {"owner"}, DENIED[OWNER_ONLY]),
    ("get", "members/", None, {"owner", "admin"}, DENIED[ADMINS]),
    ("patch", "members/{member}/", {"role": "editor"}, {"owner", "admin"}, DENIED[ADMINS]),
    ("delete", "members/{member}/", None, {"owner", "admin"}, DENIED[ADMINS]),
]


@pytest.mark.parametrize("role", ["owner", "admin", "editor", "viewer"])
@pytest.mark.parametrize("method, suffix, body, allowed, sentence", ROLE_RULES)
def test_each_action_follows_the_role_matrix_in_the_workspace_of_the_path(team, role, method, suffix, body, allowed, sentence):
    """
    Every caller also OWNS their personal workspace and sends it in the header:
    the header's role (owner) must not count. A refused role gets the 403 of
    the role permissions (only `detail`), and nothing changes.
    """
    caller = getattr(team, role)
    own = caller.owned_workspaces.exclude(pk=team.workspace.pk).first()
    headers = {"HTTP_X_WORKSPACE_ID": str(own.pk)} if own else {}
    target = suffix.format(member=team.rows["invitada"].pk)

    response = getattr(client_for(caller), method)(url(team.workspace, target), body, format="json", **headers)

    if role in allowed:
        # Allowed: it got past the role check (the bodies above make some of them a 400).
        assert response.status_code in (200, 204, 400), response.content
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": sentence}
        team.workspace.refresh_from_db()
        assert team.workspace.name != "Otro nombre"
        assert team.workspace.owner_id == team.owner.pk
        team.rows["invitada"].refresh_from_db()
        assert team.rows["invitada"].role == EDITOR


def test_the_header_of_a_workspace_where_the_user_is_only_viewer_does_not_take_the_role_away(team):
    """The other direction: owner in the path's workspace, viewer in the header's."""
    elsewhere = Workspace.objects.create(name="Otro lado", owner=new_user("jefa"))
    WorkspaceMember.objects.create(workspace=elsewhere, user=team.owner, role=VIEWER, status=ACTIVE)

    response = client_for(team.owner).patch(
        url(team.workspace), {"name": "Renombrado"}, format="json", HTTP_X_WORKSPACE_ID=str(elsewhere.pk),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renombrado"
    elsewhere.refresh_from_db()
    assert elsewhere.name == "Otro lado"


# --- rename -----------------------------------------------------------------


def test_an_admin_renames(team):
    response = client_for(team.admin).patch(url(team.workspace), {"name": "Nuevo nombre"}, format="json")

    assert response.status_code == 200
    assert response.json()["name"] == "Nuevo nombre" and response.json()["role"] == "admin"
    team.workspace.refresh_from_db()
    assert team.workspace.name == "Nuevo nombre"


# --- delete -----------------------------------------------------------------


def _project(workspace, user, status=VideoProject.Status.COMPLETED):
    return VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="Proyecto", status=status)


def test_delete_needs_the_exact_name(team):
    api = client_for(team.owner)

    missing = api.delete(url(team.workspace), {}, format="json")
    wrong = api.delete(url(team.workspace), {"confirm_name": team.workspace.name.upper() + "!"}, format="json")

    assert missing.status_code == 400 and set(missing.json()) == {"confirm_name"}
    assert wrong.status_code == 400
    assert wrong.json() == {"confirm_name": ["El nombre no coincide."]}
    assert Workspace.objects.filter(pk=team.workspace.pk).exists()


def test_delete_takes_projects_brand_kits_members_and_wallet_with_it(team):
    workspace = team.workspace
    _project(workspace, team.editor)
    BrandKit.objects.create(workspace=workspace, name="Marca")
    wallet = workspace.wallet
    wallet.available_balance = Decimal("120")
    wallet.save()
    Transaction.objects.create(wallet=wallet, amount=Decimal("5"), status=Transaction.Status.COMPLETED)
    team.viewer.last_workspace = workspace
    team.viewer.save(update_fields=["last_workspace"])

    response = client_for(team.owner).delete(url(workspace), {"confirm_name": workspace.name}, format="json")

    assert response.status_code == 204
    assert not Workspace.objects.filter(pk=workspace.pk).exists()
    assert not VideoProject.objects.filter(workspace_id=workspace.pk).exists()
    assert not BrandKit.objects.filter(workspace_id=workspace.pk).exists()
    assert not WorkspaceMember.objects.filter(workspace_id=workspace.pk).exists()
    assert not Wallet.objects.filter(pk=wallet.pk).exists()
    assert not Transaction.objects.filter(wallet_id=wallet.pk).exists()
    # Nobody's account goes, and /me of a member still answers with their own workspace.
    assert User.objects.filter(pk__in=[team.owner.pk, team.viewer.pk]).count() == 2
    me = client_for(team.viewer).get("/api/v1/me/").json()
    assert me["active_workspace_id"] == str(team.viewer.owned_workspaces.get().pk)
    # The owner has no workspace left: /me still answers.
    assert client_for(team.owner).get("/api/v1/me/").json()["workspaces"] == []


@pytest.mark.parametrize("busy", [VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING, VideoProject.Status.RENDERING])
def test_delete_is_refused_while_a_project_is_processing_or_rendering(team, busy):
    _project(team.workspace, team.editor, status=busy)

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "project_processing"
    assert "error" in response.json()
    assert Workspace.objects.filter(pk=team.workspace.pk).exists()
    assert VideoProject.objects.filter(workspace=team.workspace).count() == 1


@pytest.mark.parametrize("how", ["reserved_row", "reserved_balance"])
def test_delete_is_refused_with_tokens_reserved(team, how):
    wallet = team.workspace.wallet
    if how == "reserved_row":
        Transaction.objects.create(wallet=wallet, amount=Decimal("10"), status=Transaction.Status.RESERVED)
    else:
        wallet.reserved_balance = Decimal("10")
        wallet.save()

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "reservation_open"
    assert Workspace.objects.filter(pk=team.workspace.pk).exists()


# --- transfer ---------------------------------------------------------------


def test_transfer_makes_the_member_owner_and_the_old_owner_admin(team):
    response = client_for(team.owner).post(url(team.workspace, "transfer/"), {"new_owner_id": str(team.editor.pk)}, format="json")

    assert response.status_code == 200
    assert response.json()["owner"] == str(team.editor.pk)
    assert response.json()["role"] == "admin"
    team.workspace.refresh_from_db()
    assert team.workspace.owner_id == team.editor.pk
    old = WorkspaceMember.objects.get(workspace=team.workspace, user=team.owner)
    assert (old.role, old.status) == (ADMIN, ACTIVE)
    # Same rows as before: nobody took or freed a seat.
    assert WorkspaceMember.objects.filter(workspace=team.workspace).count() == 5
    members = client_for(team.editor).get(url(team.workspace, "members/")).json()["members"]
    assert {item["user"]["id"]: item["role"] for item in members}[str(team.editor.pk)] == "owner"
    assert {item["user"]["id"]: item["role"] for item in members}[str(team.owner.pk)] == "admin"
    # The old owner is an admin now: no second transfer, no delete.
    again = client_for(team.owner).post(url(team.workspace, "transfer/"), {"new_owner_id": str(team.owner.pk)}, format="json")
    assert again.status_code == 403


def test_transfer_keeps_the_old_owner_as_admin_even_without_a_membership_row(team):
    team.rows["owner"].delete()

    response = client_for(team.owner).post(url(team.workspace, "transfer/"), {"new_owner_id": str(team.admin.pk)}, format="json")

    assert response.status_code == 200
    old = WorkspaceMember.objects.get(workspace=team.workspace, user=team.owner)
    assert (old.role, old.status) == (ADMIN, ACTIVE)


@pytest.mark.parametrize("target", ["invitada", "stranger", "self", "unknown", "garbage", "missing"])
def test_transfer_only_to_another_active_member(team, target):
    value = {
        "invitada": str(team.invitada.pk),
        "stranger": str(new_user("ajena").pk),
        "self": str(team.owner.pk),
        "unknown": "00000000-0000-4000-8000-000000000000",
        "garbage": "no-es-un-uuid",
        "missing": None,
    }[target]
    body = {} if target == "missing" else {"new_owner_id": value}

    response = client_for(team.owner).post(url(team.workspace, "transfer/"), body, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {"new_owner_id"}
    team.workspace.refresh_from_db()
    assert team.workspace.owner_id == team.owner.pk


# --- members ----------------------------------------------------------------


def test_members_lists_active_and_pending_with_the_seats_of_the_plan(team):
    response = client_for(team.admin).get(url(team.workspace, "members/"))

    assert response.status_code == 200
    body = response.json()
    assert (body["max_members"], body["seats_used"]) == (5, 5)
    by_email = {item["email"]: item for item in body["members"]}
    assert by_email[team.owner.email]["role"] == "owner"
    assert by_email["admin@test.com"]["role"] == "admin"
    assert by_email["invitada@test.com"]["status"] == "pending"
    editor = by_email["editor@test.com"]
    assert editor == {
        "id": str(team.rows["editor"].pk),
        "user": {"id": str(team.editor.pk), "name": "Editor", "email": "editor@test.com"},
        "email": "editor@test.com",
        "role": "editor",
        "status": "active",
        "joined_at": editor["joined_at"],
        "invited_by": None,
    }


def test_without_a_plan_the_workspace_has_one_seat(user, workspace):
    Workspace.objects.filter(pk=workspace.pk).update(subscription_plan=None)

    body = client_for(user).get(url(workspace, "members/")).json()

    assert (body["max_members"], body["seats_used"]) == (1, 1)


def test_an_admin_changes_a_role_and_a_removed_member_frees_the_seat(team):
    api = client_for(team.admin)

    changed = api.patch(member_url(team.workspace, team.rows["viewer"]), {"role": "editor"}, format="json")
    removed = api.delete(member_url(team.workspace, team.rows["editor"]))
    cancelled = api.delete(member_url(team.workspace, team.rows["invitada"]))

    assert changed.status_code == 200
    assert changed.json()["role"] == "editor" and changed.json()["id"] == str(team.rows["viewer"].pk)
    assert removed.status_code == 204 and cancelled.status_code == 204
    assert not WorkspaceMember.objects.filter(pk__in=[team.rows["editor"].pk, team.rows["invitada"].pk]).exists()
    assert api.get(url(team.workspace, "members/")).json()["seats_used"] == 3
    # The removed editor is out at once: the workspace is a 404 for them.
    assert client_for(team.editor).get(url(team.workspace)).status_code == 404
    # And the seats freed can be taken again (signals.check_workspace_member_limit).
    WorkspaceMember.objects.create(workspace=team.workspace, user=new_user("nueva"), role=EDITOR)


@pytest.mark.parametrize("role", ["owner", "dueño", "", None])
def test_a_role_that_cannot_be_assigned_is_a_400(team, role):
    response = client_for(team.owner).patch(member_url(team.workspace, team.rows["editor"]), {"role": role}, format="json")

    assert response.status_code == 400
    assert response.json() == {"role": ["Elegí un rol: Admin, Editor o Lector."]}
    team.rows["editor"].refresh_from_db()
    assert team.rows["editor"].role == EDITOR


@pytest.mark.parametrize("caller", ["owner", "admin"])
def test_the_owner_is_never_demoted_or_removed_through_members(team, caller):
    """Not by an admin, and not by the owner (who would leave the workspace without one)."""
    api = client_for(getattr(team, caller))
    row = team.rows["owner"]

    demoted = api.patch(member_url(team.workspace, row), {"role": "viewer"}, format="json")
    removed = api.delete(member_url(team.workspace, row))

    assert demoted.status_code == 400
    assert demoted.json() == {"role": ["No se puede cambiar el rol del dueño."]}
    assert removed.status_code == 400
    assert removed.json() == {"detail": "No se puede quitar al dueño del workspace."}
    row.refresh_from_db()
    assert (row.role, row.status) == (ADMIN, ACTIVE)
    team.workspace.refresh_from_db()
    assert team.workspace.owner_id == team.owner.pk


def test_a_member_of_another_workspace_cannot_be_reached_through_this_one(team):
    """An admin here names a member row of a workspace they are not in: 404, untouched."""
    foreign_owner = new_user("jefa")
    foreign = foreign_owner.owned_workspaces.get()
    foreign.subscription_plan = team.workspace.subscription_plan
    foreign.save(update_fields=["subscription_plan"])
    row = WorkspaceMember.objects.create(workspace=foreign, user=new_user("lejana"), role=EDITOR, status=ACTIVE)
    api = client_for(team.admin)

    changed = api.patch(member_url(team.workspace, row), {"role": "admin"}, format="json")
    removed = api.delete(member_url(team.workspace, row))
    garbage = api.delete(url(team.workspace, "members/no-es-un-uuid/"))

    assert (changed.status_code, removed.status_code, garbage.status_code) == (404, 404, 404)
    row.refresh_from_db()
    assert row.role == EDITOR


def test_an_admin_can_leave_but_an_editor_cannot_remove_themselves(team):
    assert client_for(team.editor).delete(member_url(team.workspace, team.rows["editor"])).status_code == 403
    assert client_for(team.admin).delete(member_url(team.workspace, team.rows["admin"])).status_code == 204
    assert client_for(team.admin).get(url(team.workspace)).status_code == 404
