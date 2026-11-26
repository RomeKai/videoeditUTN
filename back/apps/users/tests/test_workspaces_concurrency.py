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
