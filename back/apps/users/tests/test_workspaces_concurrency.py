"""
Two requests that change the owner of one workspace at the same time run one
after the other: require_workspace_role(lock=True) takes the workspace's row
lock before it reads the role, so the second one sees what the first one did.
A token purchase, or a plan change, and a delete of its workspace also run
one after the other.
"""
import threading
import time
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from apps.payments.models import Payment, SubscriptionPlan
from apps.payments.services.payment_providers import SimulatedProvider
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


@override_settings(PAYMENTS_PROVIDER="simulated", PAYMENTS_ALLOW_SIMULATED=True)
class ConcurrentPurchaseAndDeleteTests(APITransactionTestCase):
    """
    A token purchase (or a plan change) starts while the owner deletes the
    workspace. Both lock the workspace row first: the delete either sees the
    pending payment and refuses, or ends first and the purchase finds no
    workspace (404). Never a 204 that leaves a pending payment behind or
    erases one, and never a 500.
    """

    def setUp(self):
        self.owner = User.objects.create_user(username="compra", email="compra@test.com", password="password")
        self.workspace = self.owner.owned_workspaces.get()
        # Another workspace of the owner's, so the delete is not refused as their last one.
        Workspace.objects.create(name="Otro", owner=self.owner)

    def _api(self):
        api = APIClient()
        api.force_authenticate(user=self.owner)
        api.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        return api

    def _purchase(self, answers):
        try:
            answers["purchase"] = self._api().post(
                "/api/v1/token-purchases/",
                {"package_id": "pack-1000", "return_url": "http://localhost:3000/uso/comprar/resultado"},
                format="json",
            ).status_code
        except Exception as exc:  # what the view did not answer (a 500)
            answers["purchase"] = type(exc).__name__
        finally:
            connection.close()

    def _delete(self, answers):
        try:
            response = self._api().delete(
                f"/api/v1/workspaces/{self.workspace.pk}/", {"confirm_name": self.workspace.name}, format="json",
            )
            answers["delete"] = (response.status_code, response.json().get("code") if response.status_code == 409 else None)
        finally:
            connection.close()

    def _someone_waits_for_a_lock(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return True
            time.sleep(0.05)
        return False

    def _assert_no_payment_lost(self, answers):
        payments = list(Payment.objects.filter(workspace_id=self.workspace.pk).values_list("status", flat=True))
        if answers["delete"][0] == 204:
            # Gone with nothing pending: the purchase never wrote its payment.
            assert answers["purchase"] == 404, answers
            assert not Payment.objects.exists()
        else:
            assert answers["delete"] == (409, "payment_pending"), answers
            assert answers["purchase"] == 201, answers
            assert payments == [Payment.Status.PENDING]
            assert Workspace.objects.filter(pk=self.workspace.pk).exists()

    def test_a_purchase_that_started_first_keeps_the_workspace(self):
        in_checkout = threading.Event()
        release = threading.Event()
        start = SimulatedProvider.start_checkout

        def slow_checkout(provider, payment, return_url):
            # The payment is written and pending; the provider has not answered yet.
            in_checkout.set()
            release.wait(timeout=10)
            return start(provider, payment, return_url)

        answers = {}
        with mock.patch.object(SimulatedProvider, "start_checkout", slow_checkout):
            purchase = threading.Thread(target=self._purchase, args=(answers,))
            purchase.start()
            assert in_checkout.wait(timeout=10)
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            delete.join(timeout=30)
            release.set()
            purchase.join(timeout=30)

        assert answers["delete"] == (409, "payment_pending")
        self._assert_no_payment_lost(answers)

    def test_a_purchase_that_comes_while_the_delete_holds_the_workspace_finds_it_gone(self):
        from apps.users import views_workspaces

        delete_locked = threading.Event()
        release = threading.Event()
        count = views_workspaces._other_workspaces

        def hold_the_lock(user, workspace):
            # The delete holds the workspace's row lock here, and its checks are still ahead.
            delete_locked.set()
            release.wait(timeout=10)
            return count(user, workspace)

        answers = {}
        with mock.patch.object(views_workspaces, "_other_workspaces", hold_the_lock):
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            assert delete_locked.wait(timeout=10)
            purchase = threading.Thread(target=self._purchase, args=(answers,))
            purchase.start()
            # The purchase waits for the workspace's row instead of writing its payment.
            assert self._someone_waits_for_a_lock()
            release.set()
            delete.join(timeout=30)
            purchase.join(timeout=30)

        assert answers["delete"] == (204, None)
        self._assert_no_payment_lost(answers)

    def test_a_plan_change_that_comes_while_the_delete_holds_the_workspace_finds_it_gone(self):
        from apps.users import views_workspaces

        plan = SubscriptionPlan.objects.create(
            name="Pago", max_members=5, price_usd_monthly=Decimal("49"), is_active=True, is_public=True,
        )
        delete_locked = threading.Event()
        release = threading.Event()
        count = views_workspaces._other_workspaces

        def hold_the_lock(user, workspace):
            delete_locked.set()
            release.wait(timeout=10)
            return count(user, workspace)

        def change():
            try:
                answers["change"] = self._api().post(
                    "/api/v1/subscription/change/", {"plan_id": str(plan.pk)}, format="json",
                ).status_code
            except Exception as exc:  # what the view did not answer (a 500)
                answers["change"] = type(exc).__name__
            finally:
                connection.close()

        answers = {}
        with mock.patch.object(views_workspaces, "_other_workspaces", hold_the_lock):
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            assert delete_locked.wait(timeout=10)
            changer = threading.Thread(target=change)
            changer.start()
            # The plan change waits for the workspace's row (lock_workspace).
            assert self._someone_waits_for_a_lock()
            release.set()
            delete.join(timeout=30)
            changer.join(timeout=30)

        assert answers == {"delete": (204, None), "change": 404}
        assert not Payment.objects.exists()
