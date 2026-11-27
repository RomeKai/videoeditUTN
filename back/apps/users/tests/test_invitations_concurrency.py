"""
Accepting and inviting take the workspace's row lock first (then the
invitation's), like every members endpoint: two requests for one link, or for
the last seat, run one after the other and the second sees what the first did.
"""
import threading
from unittest import mock

from django.conf import settings
from django.core import mail
from django.db import connection
from rest_framework.test import APIClient, APITransactionTestCase

from apps.payments.models import SubscriptionPlan
from apps.users import views_invitations
from apps.users.models import User, WorkspaceMember

ACTIVE = WorkspaceMember.Status.ACTIVE
PENDING = WorkspaceMember.Status.PENDING


def _body(name):
    return {"name": name, "password": "una-clave-Nueva-2026", "terms_accepted": True}


class ConcurrentInvitationTests(APITransactionTestCase):
    def setUp(self):
        SubscriptionPlan.objects.get_or_create(name=settings.DEFAULT_PLAN_NAME)
        self.owner = User.objects.create_user(username="duena", email="duena@test.com", password="password")
        self.workspace = self.owner.owned_workspaces.get()
        self.plan = SubscriptionPlan.objects.create(name="Equipo concurrente", max_members=5)
        self.workspace.subscription_plan = self.plan
        self.workspace.save(update_fields=["subscription_plan"])
        self.api = APIClient()
        self.api.force_authenticate(user=self.owner)

    def _invite(self, email):
        response = self.api.post(
            f"/api/v1/workspaces/{self.workspace.pk}/invitations/", {"emails": [email], "role": "editor"}, format="json",
        )
        assert response.status_code == 201
        link = next(word for word in mail.outbox[-1].body.split() if "/invitacion/" in word)
        return link.rsplit("/", 1)[1]

    def _together(self, check, *calls):
        """
        Runs the calls in threads. Each one stops at its seat check (which
        comes after the locks) until the other gets there too, or for two
        seconds: with the workspace locked the second never gets there while
        the first waits, so the first goes on alone.
        """
        meeting = threading.Barrier(len(calls))
        real = getattr(views_invitations, check)

        def wait_for_the_other(*args, **kwargs):
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return real(*args, **kwargs)

        answers = [None] * len(calls)

        def run(index, call):
            try:
                answers[index] = call().status_code
            finally:
                connection.close()

        with mock.patch.object(views_invitations, check, wait_for_the_other):
            threads = [threading.Thread(target=run, args=(index, call)) for index, call in enumerate(calls)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        return answers

    def test_two_accepts_of_one_link_create_one_account_and_one_membership(self):
        token = self._invite("juana@example.com")
        url = f"/api/v1/invitations/{token}/accept/"

        answers = self._together(
            "check_seat_for_accept",
            lambda: APIClient().post(url, _body("Juana Uno"), format="json"),
            lambda: APIClient().post(url, _body("Juana Dos"), format="json"),
        )

        assert sorted(answers) == [200, 410]
        assert User.objects.filter(email="juana@example.com").count() == 1
        juana = User.objects.get(email="juana@example.com")
        assert WorkspaceMember.objects.filter(workspace=self.workspace, user=juana, status=ACTIVE).count() == 1
        assert WorkspaceMember.objects.filter(workspace=self.workspace).count() == 2

    def test_two_accepts_for_the_last_seat_let_one_in(self):
        tokens = [self._invite("una@example.com"), self._invite("dos@example.com")]
        # The plan shrank after both were invited: room for the owner and one more.
        SubscriptionPlan.objects.filter(pk=self.plan.pk).update(max_members=2)

        answers = self._together(
            "check_seat_for_accept",
            *[
                (lambda token=token: APIClient().post(f"/api/v1/invitations/{token}/accept/", _body("Persona"), format="json"))
                for token in tokens
            ],
        )

        assert sorted(answers) == [200, 403]
        assert WorkspaceMember.objects.filter(workspace=self.workspace, status=ACTIVE).count() == 2
        assert WorkspaceMember.objects.filter(workspace=self.workspace, status=PENDING).count() == 1
        # The one refused left no account behind.
        assert User.objects.filter(email__in=["una@example.com", "dos@example.com"]).count() == 1

    def test_two_invitations_for_the_last_seat_send_one(self):
        SubscriptionPlan.objects.filter(pk=self.plan.pk).update(max_members=2)
        url = f"/api/v1/workspaces/{self.workspace.pk}/invitations/"

        def invite(email):
            api = APIClient()
            api.force_authenticate(user=self.owner)
            return api.post(url, {"emails": [email], "role": "editor"}, format="json")

        # Celery's eager app is the main thread's: the threads only note what they would queue.
        queued = []
        with mock.patch.object(views_invitations, "_queue", queued.append):
            answers = self._together(
                "check_seat_limit", lambda: invite("una@example.com"), lambda: invite("dos@example.com"),
            )

        assert sorted(answers) == [201, 403]
        assert WorkspaceMember.objects.filter(workspace=self.workspace).count() == 2
        assert queued == [WorkspaceMember.objects.get(workspace=self.workspace, status=PENDING).pk]
