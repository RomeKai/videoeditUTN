"""
check_workspace_member_limit (users/signals.py): un miembro nuevo no puede
pasar los lugares del plan (max_members), y sin plan hay un solo lugar. Las
invitaciones pendientes ocupan lugar. Antes no corría nunca: miraba
instance.id, que con el UUID por default ya existe antes del INSERT.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace, WorkspaceMember

User = get_user_model()

ACTIVE, PENDING = WorkspaceMember.Status.ACTIVE, WorkspaceMember.Status.PENDING
ADMIN, EDITOR, VIEWER = WorkspaceMember.Role.ADMIN, WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER


def make_user(name):
    return User.objects.create_user(username=name, email=f"{name}@test.com", password="pw")


def join(workspace, user, status=ACTIVE, role=EDITOR):
    return WorkspaceMember.objects.create(workspace=workspace, user=user, role=role, status=status)


class MemberLimitTests(TestCase):
    def setUp(self):
        self.owner = make_user("duena")
        # The personal workspace of the user signal, with its owner as ADMIN.
        self.workspace = self.owner.owned_workspaces.get()

    def give_plan(self, seats):
        self.workspace.subscription_plan = SubscriptionPlan.objects.create(name=f"{seats} lugares", max_members=seats)
        self.workspace.save(update_fields=["subscription_plan"])

    def test_a_new_member_over_the_plan_seats_is_refused(self):
        self.give_plan(2)
        join(self.workspace, make_user("uno"))

        with self.assertRaisesMessage(ValidationError, "solo permite 2 miembros"):
            join(self.workspace, make_user("dos"))
        self.assertEqual(WorkspaceMember.objects.filter(workspace=self.workspace).count(), 2)

    def test_a_pending_invitation_takes_a_seat(self):
        self.give_plan(2)
        join(self.workspace, make_user("invitada"), status=PENDING)

        with self.assertRaises(ValidationError):
            join(self.workspace, make_user("otra"))

    def test_without_a_plan_the_workspace_has_one_seat(self):
        self.assertIsNone(self.workspace.subscription_plan)

        with self.assertRaisesMessage(ValidationError, "no tiene un plan activo"):
            join(self.workspace, make_user("sobra"))

    def test_editing_a_member_at_the_limit_is_not_a_new_seat(self):
        self.give_plan(2)
        member = join(self.workspace, make_user("uno"), status=PENDING)

        member.role, member.status = VIEWER, ACTIVE
        member.save()

        member.refresh_from_db()
        self.assertEqual((member.role, member.status), (VIEWER, ACTIVE))

    def test_a_new_user_gets_their_own_membership_without_a_plan(self):
        user = make_user("nueva")

        workspace = user.owned_workspaces.get()
        self.assertIsNone(workspace.subscription_plan)
        member = WorkspaceMember.objects.get(workspace=workspace, user=user)
        self.assertEqual((member.role, member.status), (ADMIN, ACTIVE))

    def test_the_owner_always_gets_a_membership_in_their_workspace(self):
        """A workspace created by hand, with a member before its owner (the admin can do it)."""
        workspace = Workspace.objects.create(name="Agencia", owner=self.owner)
        join(workspace, make_user("cliente"))

        join(workspace, self.owner, role=ADMIN)

        self.assertTrue(WorkspaceMember.objects.filter(workspace=workspace, user=self.owner).exists())
