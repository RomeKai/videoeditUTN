"""
/api/v1/workspaces/: the role is the one in the workspace of the PATH (never
the header's), non-members get 404, and the owner only changes by transfer.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.permissions import ADMINS, DENIED, OWNER_ONLY
from apps.core.workspace import NOT_FOUND_DETAIL
from apps.payments.models import Payment, SubscriptionPlan, Transaction, Wallet
from apps.payments.services.payments import settle_payment
from apps.users.models import User, Workspace, WorkspaceMember
from apps.videos.models import BrandKit, ScheduledPost, VideoClip, VideoProject

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
    # A second workspace of the owner's, newer: deleting the team's is not deleting their last one.
    team.spare = Workspace.objects.create(name="Otro espacio de la dueña", owner=user)
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
    own = caller.owned_workspaces.exclude(pk=team.workspace.pk).first()  # the owner's spare one
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


def test_delete_takes_projects_posts_brand_kits_members_and_wallet_with_it(team, caplog):
    workspace = team.workspace
    _project(workspace, team.editor)
    BrandKit.objects.create(workspace=workspace, name="Marca")
    project = VideoProject.objects.get(workspace=workspace)
    clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)
    for post_status in (ScheduledPost.Status.DRAFT, ScheduledPost.Status.SCHEDULED, ScheduledPost.Status.PUBLISHED, ScheduledPost.Status.FAILED):
        ScheduledPost.objects.create(video_clip=clip, platform=ScheduledPost.Platform.TIKTOK, publish_at="2030-01-01T00:00:00Z", status=post_status)
    wallet = workspace.wallet
    Transaction.objects.create(wallet=wallet, amount=Decimal("5"), status=Transaction.Status.COMPLETED)
    team.viewer.last_workspace = workspace
    team.viewer.save(update_fields=["last_workspace"])

    with caplog.at_level("WARNING", logger="apps.users.views_workspaces"):
        response = client_for(team.owner).delete(url(workspace), {"confirm_name": workspace.name}, format="json")

    assert response.status_code == 204
    assert not Workspace.objects.filter(pk=workspace.pk).exists()
    assert not ScheduledPost.objects.filter(video_clip=clip).exists()
    # The wallet's last figures stay in the log.
    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("workspace.deleted"))
    assert f"workspace_id={workspace.pk}" in line
    assert "wallet_available=0 wallet_reserved=0 wallet_transactions=1 projects=1 posts=4" in line
    assert not VideoProject.objects.filter(workspace_id=workspace.pk).exists()
    assert not BrandKit.objects.filter(workspace_id=workspace.pk).exists()
    assert not WorkspaceMember.objects.filter(workspace_id=workspace.pk).exists()
    assert not Wallet.objects.filter(pk=wallet.pk).exists()
    assert not Transaction.objects.filter(wallet_id=wallet.pk).exists()
    # Nobody's account goes, and /me of a member still answers with their own workspace.
    assert User.objects.filter(pk__in=[team.owner.pk, team.viewer.pk]).count() == 2
    me = client_for(team.viewer).get("/api/v1/me/").json()
    assert me["active_workspace_id"] == str(team.viewer.owned_workspaces.get().pk)
    # The owner keeps their other workspace.
    assert [item["id"] for item in client_for(team.owner).get("/api/v1/me/").json()["workspaces"]] == [str(team.spare.pk)]


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


def _payment(team, kind="tokens"):
    """A payment waiting for its provider, its checkout open: a token purchase or the payment of a plan."""
    if kind == "tokens":
        fields = {"tokens": 1000}
    else:
        fields = {"tokens": 0, "plan": SubscriptionPlan.objects.create(name="Plan pago", max_members=5, price_usd_monthly=Decimal("49"))}
    return Payment.objects.create(
        workspace=team.workspace, created_by=team.owner, amount_usd=Decimal("10.00"), provider="simulated",
        provider_reference=f"sim_{kind}", checkout_url="https://app.example.com/dev/checkout?compra=1", **fields,
    )


@pytest.mark.parametrize("kind", ["tokens", "plan"])
def test_delete_is_refused_while_a_payment_is_pending(team, kind):
    """Its row is what the provider's confirmation settles (or logs as a late payment): it never goes with the workspace."""
    payment = _payment(team, kind)

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 409
    assert response.json() == {
        # Only what the web lets the person do: nothing cancels a token purchase.
        "error": "Hay un pago en curso: terminalo, o esperá a que venza (24 h después de abrirlo).",
        "code": "payment_pending",
    }
    assert Workspace.objects.filter(pk=team.workspace.pk).exists()
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.parametrize("approved", [True, False])
def test_once_the_payment_is_settled_the_workspace_can_be_deleted(team, approved):
    payment = _payment(team)
    settle_payment(payment.pk, approved=approved)
    if approved:
        # The tokens it credited were spent: a balance left would be its own refusal.
        Wallet.objects.filter(workspace=team.workspace).update(available_balance=Decimal("0"))

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 204
    assert not Workspace.objects.filter(pk=team.workspace.pk).exists()
    # A settled payment goes with it, its deposit too.
    assert not Payment.objects.filter(pk=payment.pk).exists()
    assert not Transaction.objects.filter(category=Transaction.Category.PURCHASE).exists()


def _post(team, status, publish_in=None, updated_ago=None):
    from datetime import timedelta

    from django.utils import timezone

    clip = VideoClip.objects.create(project=_project(team.workspace, team.editor), title="c", start_time=0, end_time=5)
    post = ScheduledPost.objects.create(
        video_clip=clip, platform=ScheduledPost.Platform.TIKTOK, status=status,
        publish_at=timezone.now() + (publish_in or timedelta(hours=1)),
    )
    if updated_ago is not None:
        ScheduledPost.objects.filter(pk=post.pk).update(updated_at=timezone.now() - updated_ago)
    return post


def _in_flight_cases():
    from datetime import timedelta

    from apps.users.views_workspaces import POST_PROCESSING_WINDOW, POST_QUEUED_GRACE

    minute = timedelta(minutes=1)
    return [
        # (status, publish_at from now, updated_at ago, refused)
        ("QUEUED", timedelta(minutes=30), None, True),            # waiting for its time
        ("QUEUED", -(POST_QUEUED_GRACE - minute), None, True),    # due, its task may be retrying
        ("QUEUED", -(POST_QUEUED_GRACE + minute), None, False),   # stuck: its task never came
        ("PROCESSING", -timedelta(days=1), timedelta(0), True),   # being sent now, however late
        ("PROCESSING", timedelta(0), POST_PROCESSING_WINDOW - minute, True),
        ("PROCESSING", timedelta(0), POST_PROCESSING_WINDOW + minute, False),  # stuck: the worker died
    ]


@pytest.mark.parametrize("case", range(6))
def test_delete_is_refused_only_while_a_post_can_be_going_out(team, case):
    status, publish_in, updated_ago, refused = _in_flight_cases()[case]
    post = _post(team, status, publish_in, updated_ago)

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    if refused:
        assert response.status_code == 409
        assert response.json()["code"] == "posts_in_flight"
        assert "Si alguna quedó trabada, se libera sola" in response.json()["error"]
        assert Workspace.objects.filter(pk=team.workspace.pk).exists()
        assert ScheduledPost.objects.filter(pk=post.pk).exists()
    else:
        # A stuck post never ends on its own: it goes with the workspace.
        assert response.status_code == 204
        assert not ScheduledPost.objects.filter(pk=post.pk).exists()


def test_the_last_workspace_refusal_comes_before_the_ones_that_pass_with_time(team):
    """Processing, a post going out, reserved tokens and balance: all true, and the answer is still last_workspace."""
    team.spare.delete()
    _project(team.workspace, team.editor, status=VideoProject.Status.RENDERING)
    _post(team, "PROCESSING")
    wallet = team.workspace.wallet
    wallet.available_balance, wallet.reserved_balance = Decimal("50"), Decimal("5")
    wallet.save()

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "last_workspace"


@pytest.mark.parametrize("balance, sentence", [("120.5", "Quedan 120.5 tokens"), ("1", "Queda 1 token "), ("0.0000000001", "Quedan 0.0000000001 tokens")])
def test_delete_never_takes_tokens_with_it(team, balance, sentence):
    wallet = team.workspace.wallet
    wallet.available_balance = Decimal(balance)
    wallet.save()

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "balance_left" and body["available_balance"] == balance
    assert body["error"].startswith(sentence)
    assert "no se pueden recuperar después de borrarlo" in body["error"]
    wallet.refresh_from_db()
    assert wallet.available_balance == Decimal(balance)
    assert Workspace.objects.filter(pk=team.workspace.pk).exists()


@pytest.mark.parametrize("elsewhere", [None, "pending", "active"])
def test_the_last_workspace_is_never_deleted(team, elsewhere):
    """The only one the owner owns or is an ACTIVE member of. A pending invitation elsewhere is not a place to work."""
    team.spare.delete()
    if elsewhere:
        other = Workspace.objects.create(name="De otra", owner=new_user("jefa"))
        WorkspaceMember.objects.create(workspace=other, user=team.owner, role=VIEWER, status=PENDING if elsewhere == "pending" else ACTIVE)

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    if elsewhere == "active":
        assert response.status_code == 204
    else:
        assert response.status_code == 409
        assert response.json()["code"] == "last_workspace"
        assert "único espacio" in response.json()["error"]
        assert Workspace.objects.filter(pk=team.workspace.pk).exists()


def test_the_stuck_project_refusal_says_what_to_do(team):
    _project(team.workspace, team.editor, status=VideoProject.Status.INGESTING)

    response = client_for(team.owner).delete(url(team.workspace), {"confirm_name": team.workspace.name}, format="json")

    assert "Si alguno quedó trabado, borrá ese proyecto primero." in response.json()["error"]


# --- create: cap and rate ---------------------------------------------------


def test_a_user_owns_at_most_ten_workspaces(user, workspace):
    for number in range(9):
        Workspace.objects.create(name=f"Espacio {number}", owner=user)
    # Being a member of someone else's does not count.
    WorkspaceMember.objects.create(workspace=Workspace.objects.create(name="Ajeno", owner=new_user("jefa")), user=user, role=ADMIN, status=ACTIVE)

    response = client_for(user).post(LIST_URL, {"name": "El once"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "workspace_limit"
    assert "máximo de 10 espacios" in response.json()["error"]
    assert Workspace.objects.filter(owner=user).count() == 10
    # One less and it fits again.
    Workspace.objects.filter(owner=user, name="Espacio 0").delete()
    assert client_for(user).post(LIST_URL, {"name": "El décimo"}, format="json").status_code == 201


def test_creating_is_throttled_per_user_and_listing_is_not(user, workspace):
    from unittest import mock

    from rest_framework.throttling import ScopedRateThrottle

    from apps.core.testing import clear_local_caches

    clear_local_caches("default")
    api = client_for(user)
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"workspace_create": "2/hour"}):
        # Refused requests count too: the limit is on attempts.
        attempts = [api.post(LIST_URL, {"name": name}, format="json").status_code for name in ("Uno", "", "Tres")]
        lists = [api.get(LIST_URL).status_code for _ in range(5)]
        blocked = api.post(LIST_URL, {"name": "Tres"}, format="json")
        other = client_for(new_user("otra")).post(LIST_URL, {"name": "De otra"}, format="json")

    assert attempts == [201, 400, 429]
    assert lists == [200] * 5
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Demasiados intentos.")
    assert other.status_code == 201


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


def test_transfer_is_refused_when_the_old_owner_would_not_fit_the_plan(team, plan):
    """Without a membership row, the old owner needs a seat once they stop being the owner."""
    team.rows["owner"].delete()
    plan.max_members = 4  # the four other rows fill it
    plan.save()

    response = client_for(team.owner).post(url(team.workspace, "transfer/"), {"new_owner_id": str(team.admin.pk)}, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {"detail"}
    assert "No quedan lugares en el plan" in response.json()["detail"]
    team.workspace.refresh_from_db()
    assert team.workspace.owner_id == team.owner.pk
    assert not WorkspaceMember.objects.filter(workspace=team.workspace, user=team.owner).exists()
    assert WorkspaceMember.objects.filter(workspace=team.workspace).count() == 4


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
