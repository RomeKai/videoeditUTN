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


class AdminSeatLimitTests(TestCase):
    """The admin inlines check the seats as a form error (SeatLimitFormSet), not a 500 from the signal."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="root", email="root@test.com", password="pw")
        self.client.force_login(self.admin)
        self.owner = make_user("duena")
        self.workspace = self.owner.owned_workspaces.get()
        self.workspace.subscription_plan = SubscriptionPlan.objects.create(name="2 lugares", max_members=2)
        self.workspace.save(update_fields=["subscription_plan"])
        self.url = f"/admin/users/workspace/{self.workspace.pk}/change/"

    def post(self, new_users, delete_existing=()):
        """The workspace form with its members inline: the current ones, plus a row per new user."""
        prefix = "workspacemember_set"
        members = list(WorkspaceMember.objects.filter(workspace=self.workspace).order_by("joined_at"))
        data = {
            "name": self.workspace.name,
            "owner": str(self.owner.pk),
            "subscription_plan": str(self.workspace.subscription_plan_id),
            "ayrshare_profile_key": "",
            f"{prefix}-TOTAL_FORMS": str(len(members) + len(new_users)),
            f"{prefix}-INITIAL_FORMS": str(len(members)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, member in enumerate(members):
            data.update({
                f"{prefix}-{index}-id": str(member.pk),
                f"{prefix}-{index}-workspace": str(self.workspace.pk),
                f"{prefix}-{index}-user": str(member.user_id),
                f"{prefix}-{index}-role": member.role,
                f"{prefix}-{index}-status": member.status,
            })
            if member.user in delete_existing:
                data[f"{prefix}-{index}-DELETE"] = "on"
        for offset, user in enumerate(new_users, start=len(members)):
            data.update({
                f"{prefix}-{offset}-workspace": str(self.workspace.pk),
                f"{prefix}-{offset}-user": str(user.pk),
                f"{prefix}-{offset}-role": EDITOR,
                f"{prefix}-{offset}-status": PENDING,
            })
        return self.client.post(self.url, data)

    def members(self):
        return set(WorkspaceMember.objects.filter(workspace=self.workspace).values_list("user__username", flat=True))

    def test_a_new_member_that_fits_is_saved(self):
        response = self.post([make_user("uno")])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.members(), {"duena", "uno"})

    def test_members_over_the_seats_are_a_form_error(self):
        response = self.post([make_user("uno"), make_user("dos")])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solo permite 2 miembros")
        self.assertEqual(self.members(), {"duena"})

    def test_a_member_deleted_in_the_same_save_frees_its_seat(self):
        join(self.workspace, make_user("uno"))

        response = self.post([make_user("dos")], delete_existing=[User.objects.get(username="uno")])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.members(), {"duena", "dos"})
