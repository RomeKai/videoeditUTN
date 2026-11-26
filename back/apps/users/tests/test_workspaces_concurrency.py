"""
Two requests that change the owner of one workspace at the same time run one
after the other: require_workspace_role(lock=True) takes the workspace's row
lock before it reads the role, so the second one sees what the first one did.
"""
import threading
from unittest import mock

from django.db import connection
from rest_framework.test import APIClient, APITransactionTestCase

from apps.payments.models import SubscriptionPlan
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.serializers_workspaces import TransferWorkspaceSerializer


class ConcurrentOwnerChangesTests(APITransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="duena", email="duena@test.com", password="password")
        self.workspace = self.owner.owned_workspaces.get()
        self.workspace.subscription_plan = SubscriptionPlan.objects.create(name="Equipo concurrente", max_members=5)
        self.workspace.save(update_fields=["subscription_plan"])
        self.members = []
        for name in ("ana", "beto"):
            person = User.objects.create_user(username=name, email=f"{name}@test.com", password="password")
            WorkspaceMember.objects.create(
                workspace=self.workspace, user=person,
                role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE,
            )
            self.members.append(person)
        # Another workspace of the owner's, so a delete is not refused as their last one.
        Workspace.objects.create(name="Otro", owner=self.owner)

    def _together(self, *calls):
        """
        Runs the calls in threads. Each one stops right after its role check
        (the first thing a view validates is its body) until the other gets
        there too, or for two seconds: with the row lock the second never gets
        there while the first waits, so the first goes on alone.
        """
        meeting = threading.Barrier(len(calls))
        validate = TransferWorkspaceSerializer.is_valid

        def wait_for_the_other(serializer, *args, **kwargs):
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return validate(serializer, *args, **kwargs)

        answers = [None] * len(calls)

        def run(index, call):
            try:
                api = APIClient()
                api.force_authenticate(user=self.owner)
                answers[index] = call(api).status_code
            finally:
                connection.close()

        with mock.patch.object(TransferWorkspaceSerializer, "is_valid", wait_for_the_other):
            threads = [threading.Thread(target=run, args=(index, call)) for index, call in enumerate(calls)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        return answers

    def test_two_transfers_at_once_give_the_workspace_to_one_person(self):
        url = f"/api/v1/workspaces/{self.workspace.pk}/transfer/"
        ana, beto = self.members

        answers = self._together(
            lambda api: api.post(url, {"new_owner_id": str(ana.pk)}, format="json"),
            lambda api: api.post(url, {"new_owner_id": str(beto.pk)}, format="json"),
        )

        # The second one finds the first one's result: the caller is an admin now.
        assert sorted(answers) == [200, 403]
        self.workspace.refresh_from_db()
        winner = ana if answers[0] == 200 else beto
        loser = beto if winner is ana else ana
        assert self.workspace.owner_id == winner.pk
        roles = dict(WorkspaceMember.objects.filter(workspace=self.workspace).values_list("user_id", "role"))
        # Only the new owner's row was promoted; the other transfer changed nothing.
        assert roles == {
            self.owner.pk: WorkspaceMember.Role.ADMIN,
            winner.pk: WorkspaceMember.Role.ADMIN,
            loser.pk: WorkspaceMember.Role.EDITOR,
        }


class ConcurrentLastWorkspaceTests(APITransactionTestCase):
    """
    An owner deletes their only two workspaces at the same time. Each delete
    locks its own workspace, so only the owner's row lock makes the second one
    count after the first: one goes, the other is their last and stays.
    """

    def setUp(self):
        self.owner = User.objects.create_user(username="sola", email="sola@test.com", password="password")
        self.first = self.owner.owned_workspaces.get()
        self.second = Workspace.objects.create(name="El segundo", owner=self.owner)

    def test_two_deletes_at_once_leave_one_workspace(self):
        from apps.users import views_workspaces

        meeting = threading.Barrier(2)
        count = views_workspaces._other_workspaces

        def count_together(user, workspace):
            # Both requests about to count: with the user's row lock the second never gets here
            # while the first waits, so the first goes on alone after two seconds.
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return count(user, workspace)

        answers = {}

        def delete(workspace):
            try:
                api = APIClient()
                api.force_authenticate(user=self.owner)
                answers[workspace.pk] = api.delete(
                    f"/api/v1/workspaces/{workspace.pk}/", {"confirm_name": workspace.name}, format="json",
                )
            finally:
                connection.close()

        with mock.patch.object(views_workspaces, "_other_workspaces", count_together):
            threads = [threading.Thread(target=delete, args=(workspace,)) for workspace in (self.first, self.second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        assert sorted(response.status_code for response in answers.values()) == [204, 409]
        refused = next(response for response in answers.values() if response.status_code == 409)
        assert refused.json()["code"] == "last_workspace"
        assert Workspace.objects.filter(owner=self.owner).count() == 1
