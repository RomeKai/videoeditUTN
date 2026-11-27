"""
Workspace invitations (apps.users.invitations, views_invitations): the link
belongs to an email, works once, and says nothing to whoever does not hold it.
"""
import hashlib
import logging
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.core.permissions import ADMINS, DENIED
from apps.core.workspace import NOT_FOUND_DETAIL
from apps.payments.models import Payment, SubscriptionPlan
from apps.users.invitations import INVITER_FALLBACK
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.tasks import send_invitation_email

pytestmark = pytest.mark.django_db

BACK = Path(__file__).resolve().parents[3]
ADMIN = WorkspaceMember.Role.ADMIN
EDITOR = WorkspaceMember.Role.EDITOR
ACTIVE = WorkspaceMember.Status.ACTIVE
PENDING = WorkspaceMember.Status.PENDING
GOOD_PASSWORD = "una-clave-Nueva-2026"
NEW_ACCOUNT = {"name": "Juana Pérez", "password": GOOD_PASSWORD, "terms_accepted": True}


def client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def new_user(name, **fields):
    return User.objects.create_user(
        username=name, email=f"{name}@test.com", password="password", first_name=name.title(), **fields,
    )


def invite_url(workspace):
    return f"/api/v1/workspaces/{workspace.pk}/invitations/"


def resend_url(workspace, member):
    return f"{invite_url(workspace)}{member.pk}/resend/"


def read(token):
    return APIClient().get(f"/api/v1/invitations/{token}/")


def accept(token, body=None, api=None):
    return (api or APIClient()).post(f"/api/v1/invitations/{token}/accept/", body or {}, format="json")


def token_of(message):
    link = next(word for word in message.body.split() if "/invitacion/" in word)
    assert link.startswith(f"{settings.FRONTEND_URL}/invitacion/")
    return link.rsplit("/", 1)[1]


@pytest.fixture
def team(db):
    SubscriptionPlan.objects.get_or_create(name=settings.DEFAULT_PLAN_NAME)
    owner = User.objects.create_user(
        username="duena", email="duena@test.com", password="password", first_name="Dana", last_name="Dueña",
    )
    workspace = owner.owned_workspaces.get()
    workspace.name = "Estudio Norte"
    workspace.subscription_plan = SubscriptionPlan.objects.create(name="Equipo de prueba", max_members=5)
    workspace.save(update_fields=["name", "subscription_plan"])
    return owner, workspace


def invite(team, email="juana@example.com", role="editor"):
    """Invites one email as the owner and returns `(row, token)`."""
    owner, workspace = team
    response = client_for(owner).post(invite_url(workspace), {"emails": [email], "role": role}, format="json")
    assert response.status_code == 201, response.json()
    member = WorkspaceMember.objects.get(pk=response.json()["invited"][0]["id"])
    return member, token_of(mail.outbox[-1])


# --- Invite ---


def test_inviting_creates_a_pending_row_and_mails_the_link(team):
    owner, workspace = team

    response = client_for(owner).post(invite_url(workspace), {"emails": ["Juana@Example.COM"], "role": "viewer"}, format="json")

    assert response.status_code == 201
    [row] = response.json()["invited"]
    member = WorkspaceMember.objects.get(pk=row["id"])
    assert row == {
        "id": str(member.pk), "user": None, "email": "Juana@example.com", "role": "viewer", "status": "pending",
        "joined_at": row["joined_at"], "invited_by": {"id": str(owner.pk), "name": "Dana Dueña"},
    }
    assert (member.user_id, member.status, member.invited_email, member.invited_by_id) == (None, PENDING, "Juana@example.com", owner.pk)
    [message] = mail.outbox
    assert message.to == ["Juana@example.com"]
    # Nothing a user typed travels in the email of the official sender.
    assert "Estudio Norte" not in message.body + message.subject
    assert "Dana" not in message.body + message.subject
    members = client_for(owner).get(f"/api/v1/workspaces/{workspace.pk}/members/").json()
    assert members["seats_used"] == 2
    assert [item["status"] for item in members["members"]] == ["active", "pending"]


def test_the_token_is_long_and_random_and_only_its_hash_is_stored(team):
    member, token = invite(team)
    other, other_token = invite(team, "otro@example.com")

    # 32 random bytes, urlsafe.
    assert len(token) >= 43 and token != other_token
    assert member.invitation_token_hash == hashlib.sha256(token.encode()).hexdigest()
    stored = WorkspaceMember.objects.filter(pk=member.pk).values().get()
    assert token not in [str(value) for value in stored.values()]


def test_the_answer_never_says_whether_the_email_has_an_account(team):
    owner, workspace = team
    new_user("conocida")

    response = client_for(owner).post(invite_url(workspace), {"emails": ["conocida@test.com", "nadie@test.com"], "role": "editor"}, format="json")

    assert response.status_code == 201
    assert [row["user"] for row in response.json()["invited"]] == [None, None]
    listed = client_for(owner).get(f"/api/v1/workspaces/{workspace.pk}/members/").json()["members"]
    assert [row["user"] for row in listed if row["status"] == "pending"] == [None, None]


@pytest.mark.parametrize("role, status", [(ADMIN, 201), (EDITOR, 403), ("viewer", 403)])
def test_only_the_owner_and_admins_invite(team, role, status):
    owner, workspace = team
    person = new_user("persona")
    WorkspaceMember.objects.create(workspace=workspace, user=person, role=role, status=ACTIVE)

    response = client_for(person).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "admin"}, format="json")

    assert response.status_code == status
    if status == 403:
        assert response.json() == {"detail": DENIED[ADMINS]}
        assert not mail.outbox


def test_an_outsider_and_a_pending_member_get_the_404_of_the_workspace(team):
    owner, workspace = team
    outsider = new_user("ajena")
    invited = new_user("invitada")
    WorkspaceMember.objects.create(workspace=workspace, user=invited, role=ADMIN, status=PENDING)

    for person in (outsider, invited):
        response = client_for(person).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "editor"}, format="json")
        assert (response.status_code, response.json()) == (404, {"detail": NOT_FOUND_DETAIL})
    assert APIClient().post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "editor"}, format="json").status_code == 401


@pytest.mark.parametrize("role", ["owner", "", None, "superadmin"])
def test_nobody_invites_an_owner(team, role):
    owner, workspace = team

    response = client_for(owner).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": role}, format="json")

    assert response.status_code == 400
    assert list(response.json()) == ["role"]
    assert not WorkspaceMember.objects.filter(workspace=workspace, status=PENDING).exists()


@pytest.mark.parametrize("emails", [[], None, "juana@example.com", ["no-es-un-email"], ["juana@example.com", ""], [{"a": 1}]])
def test_bad_emails_are_one_sentence_under_emails(team, emails):
    owner, workspace = team

    response = client_for(owner).post(invite_url(workspace), {"emails": emails, "role": "editor"}, format="json")

    assert response.status_code == 400
    [message] = response.json()["emails"]
    assert isinstance(message, str)
    assert not mail.outbox


def test_more_than_ten_emails_at_once_are_refused(team):
    owner, workspace = team
    workspace.subscription_plan.max_members = 50
    workspace.subscription_plan.save()

    response = client_for(owner).post(invite_url(workspace), {"emails": [f"p{n}@example.com" for n in range(11)], "role": "editor"}, format="json")

    assert response.status_code == 400
    assert not WorkspaceMember.objects.filter(workspace=workspace, status=PENDING).exists()


def test_the_same_email_twice_in_one_request_is_one_invitation(team):
    owner, workspace = team

    response = client_for(owner).post(invite_url(workspace), {"emails": ["juana@example.com", "JUANA@EXAMPLE.COM"], "role": "editor"}, format="json")

    assert response.status_code == 201
    assert len(response.json()["invited"]) == 1 and len(mail.outbox) == 1


@pytest.mark.parametrize("email", ["juana@example.com", "JUANA@example.com", "juana@EXAMPLE.com"])
def test_an_email_with_a_pending_invitation_is_refused(team, email):
    owner, workspace = team
    invite(team)

    response = client_for(owner).post(invite_url(workspace), {"emails": ["nueva@example.com", email], "role": "editor"}, format="json")

    assert response.status_code == 400
    assert list(response.json()) == ["emails"]
    # All or none: the other email of the request was not invited either.
    assert WorkspaceMember.objects.filter(workspace=workspace, status=PENDING).count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.parametrize("email", ["miembro@test.com", "MIEMBRO@test.com", "duena@test.com"])
def test_an_email_that_is_already_a_member_is_refused(team, email):
    owner, workspace = team
    WorkspaceMember.objects.create(workspace=workspace, user=new_user("miembro"), role=EDITOR, status=ACTIVE)

    response = client_for(owner).post(invite_url(workspace), {"emails": [email], "role": "editor"}, format="json")

    assert response.status_code == 400
    assert list(response.json()) == ["emails"]
    assert not mail.outbox


def test_an_owner_without_a_member_row_cannot_be_invited_either(team):
    owner, workspace = team
    WorkspaceMember.objects.filter(workspace=workspace, user=owner).delete()

    response = client_for(owner).post(invite_url(workspace), {"emails": ["duena@test.com"], "role": "editor"}, format="json")

    assert response.status_code == 400


def test_pending_invitations_take_seats_and_a_full_plan_refuses_all_of_the_request(team):
    owner, workspace = team
    workspace.subscription_plan.max_members = 3
    workspace.subscription_plan.save()
    invite(team, "una@example.com")
    mail.outbox.clear()

    both = client_for(owner).post(invite_url(workspace), {"emails": ["dos@example.com", "tres@example.com"], "role": "editor"}, format="json")

    assert both.status_code == 403
    assert both.json()["code"] == "seats_exceeded" and "Equipo de prueba" in both.json()["error"]
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 2 and not mail.outbox
    # The last seat is still there for one.
    one = client_for(owner).post(invite_url(workspace), {"emails": ["dos@example.com"], "role": "editor"}, format="json")
    assert one.status_code == 201


def test_a_pending_plan_payment_counts_the_seats_of_its_plan(team):
    owner, workspace = team
    small = SubscriptionPlan.objects.create(name="Chico", max_members=1)
    Payment.objects.create(
        workspace=workspace, created_by=owner, amount_usd=Decimal("10.00"), provider="simulated",
        provider_reference="sim_plan", checkout_url="https://app.example.com/dev/checkout?compra=1",
        plan=small, status=Payment.Status.PENDING,
    )

    response = client_for(owner).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "editor"}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "seats_exceeded" and "Chico" in response.json()["error"]


def test_a_queue_that_is_down_keeps_the_invitation_for_a_resend(team):
    from kombu.exceptions import OperationalError

    owner, workspace = team
    with mock.patch.object(send_invitation_email, "delay", side_effect=OperationalError("down")):
        response = client_for(owner).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "editor"}, format="json")
        member = WorkspaceMember.objects.get(workspace=workspace, status=PENDING)
        failed = client_for(owner).post(resend_url(workspace, member))

    assert response.status_code == 201 and failed.status_code == 502
    assert member.invitation_token_hash is None
    assert client_for(owner).post(resend_url(workspace, member)).status_code == 204
    assert read(token_of(mail.outbox[-1])).status_code == 200


# --- Resend ---


def test_a_resend_issues_a_new_link_and_kills_the_old_one(team):
    owner, workspace = team
    member, old = invite(team)

    response = client_for(owner).post(resend_url(workspace, member))

    assert response.status_code == 204 and response.content == b""
    new = token_of(mail.outbox[-1])
    assert new != old and len(mail.outbox) == 2
    assert read(old).status_code == 404
    assert accept(old, NEW_ACCOUNT).status_code == 410
    assert read(new).status_code == 200
    member.refresh_from_db()
    assert member.status == PENDING


def test_the_old_link_dies_even_if_the_new_email_is_never_sent(team):
    owner, workspace = team
    member, old = invite(team)

    with mock.patch.object(send_invitation_email, "delay"):
        assert client_for(owner).post(resend_url(workspace, member)).status_code == 204

    assert read(old).status_code == 404


def test_a_resend_starts_the_expiry_again(team):
    owner, workspace = team
    member, old = invite(team)
    WorkspaceMember.objects.filter(pk=member.pk).update(invitation_sent_at=timezone.now() - timedelta(days=30))

    client_for(owner).post(resend_url(workspace, member))

    assert read(token_of(mail.outbox[-1])).status_code == 200


def test_resend_is_a_404_for_what_is_not_a_pending_invitation_of_that_workspace(team):
    owner, workspace = team
    member, token = invite(team)
    active = WorkspaceMember.objects.create(workspace=workspace, user=new_user("activa"), role=EDITOR, status=ACTIVE)
    other_owner = new_user("otra")
    mail.outbox.clear()

    assert client_for(owner).post(resend_url(workspace, active)).status_code == 404
    assert client_for(owner).post(f"{invite_url(workspace)}no-es-un-id/resend/").status_code == 404
    # Someone else's workspace, with a real invitation id of another one.
    assert client_for(other_owner).post(resend_url(other_owner.owned_workspaces.get(), member)).status_code == 404
    assert client_for(other_owner).post(resend_url(workspace, member)).status_code == 404
    accepted = accept(token, NEW_ACCOUNT)
    assert accepted.status_code == 200
    assert client_for(owner).post(resend_url(workspace, member)).status_code == 404
    assert not mail.outbox
    assert read(token).status_code == 404


def test_an_editor_cannot_resend(team):
    owner, workspace = team
    member, token = invite(team)
    editor = new_user("editora")
    WorkspaceMember.objects.create(workspace=workspace, user=editor, role=EDITOR, status=ACTIVE)

    assert client_for(editor).post(resend_url(workspace, member)).status_code == 403
    assert read(token).status_code == 200


# --- Read ---


def test_the_link_shows_who_invites_where_and_as_what_but_never_the_inviters_email(team):
    owner, workspace = team
    member, token = invite(team, role="admin")

    response = read(token)

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": str(workspace.pk), "workspace_name": "Estudio Norte", "inviter_name": "Dana Dueña",
        "role": "admin", "email": "juana@example.com", "status": "pending", "has_account": False,
    }
    assert owner.email not in response.content.decode()


def test_an_inviter_without_a_name_is_never_shown_by_their_email(team):
    owner, workspace = team
    User.objects.filter(pk=owner.pk).update(first_name="", last_name="")
    member, token = invite(team)

    body = read(token).json()

    assert body["inviter_name"] == INVITER_FALLBACK
    assert "duena" not in str(body)


def test_the_link_says_when_the_email_already_has_an_account(team):
    new_user("juana")
    member, token = invite(team, "JUANA@test.com")

    assert read(token).json()["has_account"] is True


def test_a_bad_an_expired_a_used_and_a_revoked_link_are_the_same_404(team, settings):
    owner, workspace = team
    expired, expired_token = invite(team, "vencida@example.com")
    WorkspaceMember.objects.filter(pk=expired.pk).update(
        invitation_sent_at=timezone.now() - timedelta(seconds=settings.INVITATION_MAX_AGE_SECONDS + 5),
    )
    used, used_token = invite(team, "usada@example.com")
    assert accept(used_token, NEW_ACCOUNT).status_code == 200
    revoked, revoked_token = invite(team, "revocada@example.com")
    assert client_for(owner).delete(f"/api/v1/workspaces/{workspace.pk}/members/{revoked.pk}/").status_code == 204
    live, live_token = invite(team, "viva@example.com")

    answers = [read(token) for token in ("no-existe", "x" * 43, expired_token, used_token, revoked_token)]

    assert {(answer.status_code, answer.content) for answer in answers} == {(404, answers[0].content)}
    gone = [accept(token, NEW_ACCOUNT) for token in ("no-existe", expired_token, used_token, revoked_token)]
    assert {(answer.status_code, answer.content) for answer in gone} == {(410, gone[0].content)}
    assert gone[0].json()["code"] == "invitation_invalid"
    assert read(live_token).status_code == 200
    # Nothing was created or activated by the dead links.
    assert not User.objects.filter(email__in=["vencida@example.com", "revocada@example.com"]).exists()
    expired.refresh_from_db()
    assert expired.status == PENDING


def test_the_link_lasts_what_the_setting_says(team, settings):
    member, token = invite(team)
    WorkspaceMember.objects.filter(pk=member.pk).update(invitation_sent_at=timezone.now() - timedelta(seconds=90))

    settings.INVITATION_MAX_AGE_SECONDS = 60
    assert read(token).status_code == 404
    settings.INVITATION_MAX_AGE_SECONDS = 120
    assert read(token).status_code == 200


def test_invitations_last_seven_days_by_default_and_the_setting_must_be_positive():
    env = {key: item for key, item in os.environ.items() if key != "INVITATION_MAX_AGE_SECONDS"}
    code = "import django; django.setup(); from django.conf import settings; print(settings.INVITATION_MAX_AGE_SECONDS)"
    default = subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)
    assert default.returncode == 0, default.stderr
    assert default.stdout.strip() == str(7 * 24 * 3600)
    for value in ("0", "-5"):
        result = subprocess.run([sys.executable, "-c", code], env={**env, "INVITATION_MAX_AGE_SECONDS": value}, cwd=BACK, capture_output=True, text=True)
        assert result.returncode != 0
        assert f"ImproperlyConfigured: INVITATION_MAX_AGE_SECONDS={value} must be a positive number of seconds" in result.stderr


# --- Accept with a session ---


def test_the_account_of_the_invited_email_accepts_with_its_session(team):
    owner, workspace = team
    juana = new_user("juana")
    member, token = invite(team, "Juana@TEST.com", role="viewer")

    response = accept(token, api=client_for(juana))

    assert (response.status_code, response.json()) == (200, {"workspace_id": str(workspace.pk), "tokens": None})
    member.refresh_from_db()
    assert (member.user_id, member.status, member.role) == (juana.pk, ACTIVE, "viewer")
    assert (member.invitation_token_hash, member.invitation_sent_at) == (None, None)
    assert client_for(juana).get(f"/api/v1/workspaces/{workspace.pk}/").json()["role"] == "viewer"
    # Once.
    assert accept(token, api=client_for(juana)).status_code == 410
    assert read(token).status_code == 404


def test_another_account_cannot_accept_the_invitation_of_someone_elses_email(team):
    owner, workspace = team
    new_user("juana")
    intruder = new_user("intrusa")
    member, token = invite(team, "juana@test.com", role="admin")

    response = accept(token, NEW_ACCOUNT, api=client_for(intruder))

    assert response.status_code == 403
    assert response.json()["code"] == "invitation_email_mismatch"
    member.refresh_from_db()
    assert (member.user_id, member.status) == (None, PENDING)
    assert not WorkspaceMember.objects.filter(workspace=workspace, user=intruder).exists()
    assert client_for(intruder).get(f"/api/v1/workspaces/{workspace.pk}/").status_code == 404
    # The link still works for its owner.
    assert read(token).status_code == 200


def test_a_session_cannot_take_the_invitation_of_an_email_without_an_account(team):
    intruder = new_user("intrusa")
    member, token = invite(team, "sin-cuenta@example.com")

    response = accept(token, NEW_ACCOUNT, api=client_for(intruder))

    assert response.status_code == 403
    assert not User.objects.filter(email="sin-cuenta@example.com").exists()


def test_a_dead_session_is_a_401_and_not_a_new_account(team):
    member, token = invite(team)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer no-es-un-token")

    response = accept(token, NEW_ACCOUNT, api=api)

    assert response.status_code == 401
    assert not User.objects.filter(email="juana@example.com").exists()


def test_accepting_when_already_in_spends_the_invitation_and_frees_its_seat(team):
    owner, workspace = team
    juana = new_user("juana")
    member, token = invite(team, "juana@test.com")
    # Added by hand in the admin meanwhile.
    WorkspaceMember.objects.create(workspace=workspace, user=juana, role=ADMIN, status=ACTIVE)

    response = accept(token, api=client_for(juana))

    assert response.status_code == 200
    rows = WorkspaceMember.objects.filter(workspace=workspace, user=juana)
    assert [(row.role, row.status) for row in rows] == [(ADMIN, ACTIVE)]
    assert not WorkspaceMember.objects.filter(pk=member.pk).exists()
    assert read(token).status_code == 404


# --- Accept without an account ---


def test_the_link_creates_a_verified_account_its_workspace_and_the_membership(team):
    owner, workspace = team
    member, token = invite(team, "juana@example.com", role="editor")

    response = accept(token, NEW_ACCOUNT)

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.pk)
    juana = User.objects.get(email="juana@example.com")
    assert juana.is_email_verified is True and juana.terms_accepted_at is not None
    assert (juana.first_name, juana.last_name) == ("Juana", "Pérez")
    assert juana.check_password(GOOD_PASSWORD)
    member.refresh_from_db()
    assert (member.user_id, member.status, member.invitation_token_hash) == (juana.pk, ACTIVE, None)
    # Its own workspace, like any sign-up.
    own = Workspace.objects.get(owner=juana)
    assert own.name == "Espacio de Juana"
    assert WorkspaceMember.objects.filter(workspace=own, user=juana, role=ADMIN, status=ACTIVE).exists()
    # The tokens are a session of the new account.
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
    me = api.get("/api/v1/me/").json()
    assert me["email"] == "juana@example.com" and me["is_email_verified"] is True
    assert {item["id"] for item in me["workspaces"]} == {str(workspace.pk), str(own.pk)}
    assert APIClient().post("/api/token/refresh/", {"refresh": body["tokens"]["refresh"]}, format="json").status_code == 200
    assert accept(token, NEW_ACCOUNT).status_code == 410


@pytest.mark.parametrize("changes, field", [
    ({"password": "12345678"}, "password"),
    ({"password": "corta"}, "password"),
    ({"password": "juana@example.com"}, "password"),
    ({"password": None}, "password"),
    ({"name": ""}, "name"),
    ({"name": None}, "name"),
    ({"terms_accepted": False}, "terms_accepted"),
    ({"terms_accepted": None}, "terms_accepted"),
])
def test_a_body_that_does_not_pass_the_sign_up_rules_creates_nothing(team, changes, field):
    member, token = invite(team)
    body = {key: value for key, value in {**NEW_ACCOUNT, **changes}.items() if value is not None}

    response = accept(token, body)

    assert response.status_code == 400
    assert list(response.json()) == [field]
    assert all(isinstance(message, str) for message in response.json()[field])
    assert not User.objects.filter(email="juana@example.com").exists()
    member.refresh_from_db()
    assert member.status == PENDING
    # The link was not spent.
    assert accept(token, NEW_ACCOUNT).status_code == 200


def test_an_email_with_an_account_must_sign_in_first(team):
    juana = new_user("juana")
    member, token = invite(team, "JUANA@test.com")

    response = accept(token, NEW_ACCOUNT)

    assert response.status_code == 401
    assert list(response.json()) == ["detail"]
    juana.refresh_from_db()
    assert juana.check_password("password")
    member.refresh_from_db()
    assert (member.status, member.user_id) == (PENDING, None)
    assert User.objects.filter(email__iexact="juana@test.com").count() == 1


def test_the_seats_are_checked_again_when_accepting_and_no_account_is_left_behind(team):
    owner, workspace = team
    member, token = invite(team)
    # The plan shrank after the invitation (the admin), and someone else took the seat.
    WorkspaceMember.objects.create(workspace=workspace, user=new_user("otra"), role=EDITOR, status=ACTIVE)
    SubscriptionPlan.objects.filter(pk=workspace.subscription_plan_id).update(max_members=2)

    response = accept(token, NEW_ACCOUNT)

    assert response.status_code == 403
    assert response.json()["code"] == "seats_exceeded"
    assert not User.objects.filter(email="juana@example.com").exists()
    member.refresh_from_db()
    assert member.status == PENDING
    # With room again, the same link works.
    SubscriptionPlan.objects.filter(pk=workspace.subscription_plan_id).update(max_members=3)
    assert accept(token, NEW_ACCOUNT).status_code == 200


def test_a_failure_after_creating_the_account_rolls_everything_back(team):
    member, token = invite(team)

    with mock.patch("apps.users.views_invitations.check_seat_for_accept", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            accept(token, NEW_ACCOUNT)

    assert not User.objects.filter(email="juana@example.com").exists()
    assert not Workspace.objects.filter(name="Espacio de Juana").exists()
    member.refresh_from_db()
    assert member.status == PENDING and member.invitation_token_hash is not None


# --- Throttles ---


def _rates(**rates):
    return mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, rates)


def test_invitations_are_limited_per_user_counting_resends(team):
    owner, workspace = team
    member, token = invite(team)

    # The invitation above already counted one.
    with _rates(invitation_send="3/hour"):
        first = client_for(owner).post(resend_url(workspace, member))
        second = client_for(owner).post(invite_url(workspace), {"emails": ["dos@example.com"], "role": "editor"}, format="json")
        third = client_for(owner).post(invite_url(workspace), {"emails": ["tres@example.com"], "role": "editor"}, format="json")
        fourth = client_for(owner).post(resend_url(workspace, member))

    assert [first.status_code, second.status_code, third.status_code, fourth.status_code] == [204, 201, 429, 429]
    assert "Demasiados intentos" in third.json()["detail"] and third["Retry-After"]
    assert not WorkspaceMember.objects.filter(invited_email="tres@example.com").exists()


def test_invitations_are_limited_per_workspace_whoever_sends_them(team):
    owner, workspace = team
    admin = new_user("admin")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role=ADMIN, status=ACTIVE)
    outsider = new_user("ajena")
    editor = new_user("editora")
    WorkspaceMember.objects.create(workspace=workspace, user=editor, role=EDITOR, status=ACTIVE)

    with _rates(invitation_send_workspace="2/hour"):
        # Who cannot invite there does not use up the workspace's limit.
        for person in (outsider, editor):
            for _ in range(3):
                assert client_for(person).post(invite_url(workspace), {"emails": ["x@example.com"], "role": "editor"}, format="json").status_code in (403, 404)
        first = client_for(owner).post(invite_url(workspace), {"emails": ["una@example.com"], "role": "editor"}, format="json")
        second = client_for(admin).post(invite_url(workspace), {"emails": ["dos@example.com"], "role": "editor"}, format="json")
        third = client_for(admin).post(invite_url(workspace), {"emails": ["tres@example.com"], "role": "editor"}, format="json")
        member = WorkspaceMember.objects.get(invited_email="una@example.com")
        resend = client_for(owner).post(resend_url(workspace, member))
        # Another workspace of the same user has its own count.
        other = Workspace.objects.create(name="Otro", owner=owner, subscription_plan=workspace.subscription_plan)
        elsewhere = client_for(owner).post(invite_url(other), {"emails": ["una@example.com"], "role": "editor"}, format="json")

    assert [first.status_code, second.status_code, third.status_code, resend.status_code] == [201, 201, 429, 429]
    assert elsewhere.status_code == 201


def test_reading_a_link_is_limited_per_ip(team):
    member, token = invite(team)

    with _rates(invitation_read="3/hour"):
        answers = [read("adivinado-%d" % n).status_code for n in range(3)] + [read(token).status_code]
        other_ip = APIClient().get(f"/api/v1/invitations/{token}/", REMOTE_ADDR="10.9.8.7")

    assert answers == [404, 404, 404, 429]
    assert other_ip.status_code == 200


def test_accepting_is_limited_per_ip_also_with_a_session(team):
    member, token = invite(team)
    people = [new_user(f"persona{n}") for n in range(3)]

    with _rates(invitation_accept="3/hour"):
        # Different accounts from one address share the count.
        answers = [accept(f"adivinado-{n}", api=client_for(person)).status_code for n, person in enumerate(people)]
        blocked = accept(token, NEW_ACCOUNT)
        other_ip = APIClient().post(f"/api/v1/invitations/{token}/accept/", NEW_ACCOUNT, format="json", REMOTE_ADDR="10.9.8.7")

    assert answers == [410, 410, 410]
    assert blocked.status_code == 429
    assert other_ip.status_code == 200


# --- Privacy ---


def test_the_task_gets_only_the_id_of_the_row(team):
    owner, workspace = team

    with mock.patch.object(send_invitation_email, "delay") as delay:
        response = client_for(owner).post(invite_url(workspace), {"emails": ["juana@example.com"], "role": "editor"}, format="json")
        member_id = response.json()["invited"][0]["id"]
        client_for(owner).post(resend_url(workspace, WorkspaceMember.objects.get(pk=member_id)))

    assert [(call.args, call.kwargs) for call in delay.call_args_list] == [((member_id,), {}), ((member_id,), {})]


def test_logs_carry_ids_only_never_an_email_or_a_token(team, caplog):
    import smtplib

    owner, workspace = team
    intruder = new_user("intrusa")
    caplog.set_level(logging.DEBUG)

    member, token = invite(team, "juana.privada@example.com")
    client_for(owner).post(resend_url(workspace, member))
    second = token_of(mail.outbox[-1])
    read(second)
    read("un-token-inventado")
    accept(second, NEW_ACCOUNT, api=client_for(intruder))
    accept(second, {**NEW_ACCOUNT, "password": "12345678"})
    accept(second, NEW_ACCOUNT)
    accept(second, NEW_ACCOUNT)
    with mock.patch("apps.users.invitations.send_mail", side_effect=smtplib.SMTPException("juana.privada@example.com refused")):
        other, _ = None, client_for(owner).post(invite_url(workspace), {"emails": ["otra.privada@example.com"], "role": "editor"}, format="json")
    send_invitation_email("00000000-0000-0000-0000-000000000000")

    records = [record for record in caplog.records if record.name.startswith("apps.")]
    text = "\n".join(record.getMessage() for record in records)
    assert "invitation.created" in text and "invitation.resent" in text and "invitation.accepted" in text
    assert "invitation.email_mismatch" in text and "invitation_email.send_failed" in text and "SMTPException" in text
    assert str(member.pk) in text
    for secret in ("privada", "example.com", "intrusa@", "duena@", token, second, "Juana", GOOD_PASSWORD):
        assert secret not in text


def test_the_task_sends_nothing_for_a_row_that_is_gone_or_already_active(team):
    owner, workspace = team
    member, token = invite(team)
    assert accept(token, NEW_ACCOUNT).status_code == 200
    mail.outbox.clear()

    send_invitation_email(str(member.pk))
    send_invitation_email("00000000-0000-0000-0000-000000000000")

    assert not mail.outbox
    member.refresh_from_db()
    assert member.invitation_token_hash is None


# --- The rows the model accepts ---


def test_the_database_refuses_an_active_row_without_an_account_and_two_pending_of_one_email(team):
    from django.db import IntegrityError, transaction

    owner, workspace = team
    WorkspaceMember.objects.create(workspace=workspace, status=PENDING, invited_email="juana@example.com")

    for fields in (
        {"status": ACTIVE, "invited_email": "otra@example.com"},
        {"status": PENDING, "invited_email": "JUANA@example.com"},
        {"status": PENDING, "invited_email": ""},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkspaceMember.objects.create(workspace=workspace, **fields)
