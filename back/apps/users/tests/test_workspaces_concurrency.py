"""
Two requests that change the owner of one workspace at the same time run one
after the other: require_workspace_role(lock=True) takes the workspace's row
lock before it reads the role, so the second one sees what the first one did.
A token purchase, or a plan change, and a delete of its workspace also run
one after the other. So do a brand kit made the default and a delete (both
lock the workspace row before the kit's), and a project created, rendered or
re-dispatched while its workspace is deleted: the delete locks the projects,
then the workspace and then the wallet, and those lock their project before
the wallet, so they never deadlock.
"""
import tempfile
import threading
import time
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from apps.payments.models import Payment, SubscriptionPlan, Transaction, Wallet
from apps.payments.services.payment_providers import SimulatedProvider
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.serializers_workspaces import TransferWorkspaceSerializer
from apps.videos.models import BrandKit, ScheduledPost, VideoClip, VideoProject
from apps.videos.services.media_validation import MediaInfo


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


def _waiting_for_a_lock(count=1, timeout=10):
    """Whether ``count`` connections of this test database wait for a row lock, within ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0] >= count:
                return True
        time.sleep(0.05)
    return False


class DeleteHarness:
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(username="duenia", email="duenia@test.com", password="password")
        self.workspace = self.owner.owned_workspaces.get()
        # Another workspace of the owner's, so the delete is not refused as their last one.
        Workspace.objects.create(name="Otro", owner=self.owner)

    def _api(self):
        api = APIClient()
        api.force_authenticate(user=self.owner)
        api.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        return api

    def _delete(self, answers):
        try:
            response = self._api().delete(
                f"/api/v1/workspaces/{self.workspace.pk}/", {"confirm_name": self.workspace.name}, format="json",
            )
            answers["delete"] = (response.status_code, response.json().get("code") if response.status_code == 409 else None)
        except Exception as exc:  # what the view did not answer: a deadlock is an OperationalError
            answers["delete"] = type(exc).__name__
        finally:
            connection.close()


class ConcurrentBrandKitAndDeleteTests(DeleteHarness, APITransactionTestCase):
    """
    The delete takes the brand kits with it (rows of its cascade, before the
    workspace's own), and a kit made the default locks the workspace row and
    then the kit's. The delete locks the workspace row before any kit's too
    (require_workspace_role with lock=True, after the projects): whichever gets
    there first goes on, the other one waits for it. Never a deadlock.
    """

    def setUp(self):
        super().setUp()
        self.kit = BrandKit.objects.create(workspace=self.workspace, user=self.owner, name="Marca")

    def _make_default(self, answers):
        try:
            answers["kit"] = self._api().patch(
                f"/api/v1/brand-kits/{self.kit.pk}/", {"is_default": True}, format="json",
            ).status_code
        except Exception as exc:
            answers["kit"] = type(exc).__name__
        finally:
            connection.close()

    def test_a_default_kit_that_comes_while_the_delete_holds_the_workspace_finds_it_gone(self):
        from apps.users import views_workspaces

        delete_locked = threading.Event()
        release = threading.Event()
        count = views_workspaces._other_workspaces

        def hold_the_lock(user, workspace):
            # The workspace row is locked here; the kits are deleted later, with the cascade.
            delete_locked.set()
            release.wait(timeout=10)
            return count(user, workspace)

        answers = {}
        with mock.patch.object(views_workspaces, "_other_workspaces", hold_the_lock):
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            assert delete_locked.wait(timeout=10)
            kit = threading.Thread(target=self._make_default, args=(answers,))
            kit.start()
            # The kit waits for the workspace row before it locks its own.
            assert _waiting_for_a_lock()
            release.set()
            delete.join(timeout=30)
            kit.join(timeout=30)

        assert answers == {"delete": (204, None), "kit": 404}
        assert not BrandKit.objects.exists()

    def test_a_delete_that_comes_while_a_kit_is_made_the_default_waits_for_it(self):
        from apps.videos.services import brand_kit_service

        kit_locked = threading.Event()
        release = threading.Event()
        demote = brand_kit_service.demote_other_defaults

        def hold_the_lock(workspace_id, keep_pk=None):
            # The kit's request holds the workspace row and the kit's.
            kit_locked.set()
            release.wait(timeout=10)
            return demote(workspace_id, keep_pk=keep_pk)

        answers = {}
        with mock.patch.object(brand_kit_service, "demote_other_defaults", hold_the_lock):
            kit = threading.Thread(target=self._make_default, args=(answers,))
            kit.start()
            assert kit_locked.wait(timeout=10)
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            assert _waiting_for_a_lock()
            release.set()
            kit.join(timeout=30)
            delete.join(timeout=30)

        assert answers == {"kit": 200, "delete": (204, None)}
        assert not Workspace.objects.filter(pk=self.workspace.pk).exists()


class ConcurrentCreationAndDeleteTests(DeleteHarness, APITransactionTestCase):
    """
    A project is created while the owner deletes its workspace. The delete
    locks the projects, the workspace and then the wallet; the creation's
    reservation used to lock the wallet (reserve_funds) and then wait for its
    project's row (the Transaction row's foreign key), so a delete that held
    that row and waited for the wallet deadlocked with it, and Postgres
    aborted one of the two. Now the reservation locks its project first, like
    an approval or a paid render: it waits for the delete, which sees the new
    project and refuses (project_processing), and then reserves.
    """

    def setUp(self):
        super().setUp()
        Wallet.objects.filter(workspace=self.workspace).update(available_balance=Decimal("1000"), reserved_balance=0)
        done = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.owner, title="Listo", status=VideoProject.Status.COMPLETED,
        )
        clip = VideoClip.objects.create(project=done, title="c", start_time=0, end_time=5)
        # A draft post: what the delete locks after the projects and before the wallet.
        self.post = ScheduledPost.objects.create(
            video_clip=clip, platform=ScheduledPost.Platform.TIKTOK, publish_at="2030-01-01T00:00:00Z",
            status=ScheduledPost.Status.DRAFT,
        )
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_root = override_settings(MEDIA_ROOT=media.name)
        media_root.enable()
        self.addCleanup(media_root.disable)
        probed = MediaInfo(duration_s=100.0, width=1920, height=1080, mime="video/mp4")
        for patcher in (
            mock.patch("apps.videos.services.media_validation.validate_upload", return_value=probed),
            mock.patch("apps.videos.views.process_initial_ingestion.delay"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_project_created_while_its_workspace_is_deleted_never_deadlocks(self):
        from apps.videos.services import render_pricing

        post_locked, release_post = threading.Event(), threading.Event()
        saved, go = threading.Event(), threading.Event()
        price = render_pricing.price_new_project

        def hold_the_post():
            try:
                with transaction.atomic():
                    ScheduledPost.objects.select_for_update().filter(pk=self.post.pk).first()
                    post_locked.set()
                    release_post.wait(timeout=20)
            finally:
                connection.close()

        def priced_after_the_delete_starts(*args, **kwargs):
            # The project row is saved and committed; its reservation comes next.
            saved.set()
            go.wait(timeout=20)
            return price(*args, **kwargs)

        answers = {}

        def create():
            try:
                answers["create"] = self._api().post("/api/v1/projects/", {
                    "title": "Nuevo", "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
                }, format="multipart").status_code
            except Exception as exc:
                answers["create"] = type(exc).__name__
            finally:
                connection.close()

        with mock.patch.object(render_pricing, "price_new_project", priced_after_the_delete_starts):
            holder = threading.Thread(target=hold_the_post)
            holder.start()
            assert post_locked.wait(timeout=10)
            creation = threading.Thread(target=create)
            creation.start()
            assert saved.wait(timeout=10)
            delete = threading.Thread(target=self._delete, args=(answers,))
            delete.start()
            # The delete holds every project of the workspace, the new one included, and waits for the post.
            assert _waiting_for_a_lock(1)
            go.set()
            # The reservation waits for the delete too: for its project's row, holding nothing the delete needs.
            assert _waiting_for_a_lock(2)
            release_post.set()
            for thread in (holder, delete, creation):
                thread.join(timeout=30)

        assert answers == {"delete": (409, "project_processing"), "create": 201}
        project = VideoProject.objects.get(workspace=self.workspace, title="Nuevo")
        assert Transaction.objects.filter(project=project, status=Transaction.Status.RESERVED).count() == 1
        assert Workspace.objects.filter(pk=self.workspace.pk).exists()



class ConcurrentPaidRenderAndDeleteTests(DeleteHarness, APITransactionTestCase):
    """
    A paid render (``clips/:id/render/``) or a re-dispatch starts while the
    owner deletes the workspace. Both lock the project's row; with
    ``select_related('workspace')`` and no ``of``, that FOR UPDATE also locked
    the workspace's row, after the project's: the reverse of the delete, which
    locks the workspace and then the projects, so Postgres aborted one of the
    two. Now they lock the project only: they go on while the delete holds the
    workspace, and the delete then sees what they did and refuses.
    """

    def setUp(self):
        from apps.videos.tests.fakes import FakeRedis
        from apps.videos.tests.test_approved_segments_source import CLIP_DELAY, REDIS_CLIENT

        super().setUp()
        Wallet.objects.filter(workspace=self.workspace).update(available_balance=Decimal("100"), reserved_balance=0)
        for patcher in (
            mock.patch(REDIS_CLIENT, return_value=FakeRedis()),
            mock.patch(CLIP_DELAY),
            mock.patch("apps.videos.tasks.ingestion.process_initial_ingestion.delay"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _project(self, **fields):
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.owner, title="Entregado",
            source_file="videos/raw/source.mp4", original_r2_key="videos/1/original/source.mp4",
            add_subtitles=False, metadata={"duration": 120.0}, **fields,
        )
        clip = VideoClip.objects.create(
            project=project, title="Clip", start_time=0, end_time=10,
            status=VideoClip.Status.COMPLETED, s3_object_key="videos/old.mp4",
        )
        return project, clip

    def _race(self, url, held, first):
        """
        ``url`` (a paid render or a re-dispatch) and the workspace's delete at once.
        ``first`` is the one that stops halfway, holding its locks, until the other
        one waits for a lock or ends: the delete in ``_other_workspaces`` (it holds the
        projects, the workspace's row and the owner's), or the request in ``held``
        (``(object, name)``, called with the project's row locked).
        """
        from apps.users import views_workspaces

        stopped, release = threading.Event(), threading.Event()
        target, name = (views_workspaces, "_other_workspaces") if first == "delete" else held
        original = getattr(target, name)

        def stop_here(*args, **kwargs):
            stopped.set()
            release.wait(timeout=10)
            return original(*args, **kwargs)

        answers = {}

        def post():
            try:
                answers["request"] = self._api().post(url, {}, format="json").status_code
            except Exception as exc:  # what the view did not answer: a deadlock is an OperationalError
                answers["request"] = type(exc).__name__
            finally:
                connection.close()

        runs = {"delete": lambda: self._delete(answers), "request": post}
        second = "request" if first == "delete" else "delete"
        static = isinstance(vars(target).get(name), staticmethod)
        with mock.patch.object(target, name, staticmethod(stop_here) if static else stop_here):
            one = threading.Thread(target=runs[first])
            one.start()
            assert stopped.wait(timeout=10)
            other = threading.Thread(target=runs[second])
            other.start()
            deadline = time.monotonic() + 10
            while other.is_alive() and not _waiting_for_a_lock(timeout=0.2) and time.monotonic() < deadline:
                pass
            release.set()
            for thread in (one, other):
                thread.join(timeout=30)
        return answers

    def test_a_paid_render_that_holds_its_project_and_a_delete_never_deadlock(self):
        """The render commits (taking the workspace's row: FOR KEY SHARE) while the delete waits for the project."""
        from apps.videos import views_clips

        _project, clip = self._project(status=VideoProject.Status.COMPLETED, pipeline_stage="completed")

        answers = self._race(f"/api/v1/clips/{clip.pk}/render/", (views_clips, "_price"), first="request")

        assert answers == {"request": 202, "delete": (409, "project_processing")}

    def test_a_paid_render_that_comes_while_the_delete_holds_the_project_waits_for_it(self):
        _project, clip = self._project(status=VideoProject.Status.COMPLETED, pipeline_stage="completed")

        answers = self._race(f"/api/v1/clips/{clip.pk}/render/", None, first="delete")

        # The delete saw nothing in progress (only the balance) and refused; then the render went.
        assert answers == {"delete": (409, "balance_left"), "request": 202}

    def _failed_and_refunded(self):
        """Failed during its ingestion, its creation's reservation returned: a re-dispatch reserves it again."""
        from apps.payments.services import wallet_service

        project, clip = self._project(status=VideoProject.Status.FAILED, pipeline_stage="failed")
        wallet = Wallet.objects.get(workspace=self.workspace)
        creation = wallet_service.reserve_funds(wallet.id, Decimal("10"), project=project)
        wallet_service.rollback_reservation(creation.id)
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, "reservation_id": str(creation.id)})
        return project, clip

    def test_a_redispatch_and_a_delete_never_deadlock(self):
        from apps.videos import views_clips

        project, _clip = self._failed_and_refunded()
        url = f"/api/v1/projects/{project.pk}/redispatch/"

        answers = self._race(url, (views_clips.ProjectRedispatchView, "_amount_to_reserve"), first="request")

        assert answers == {"request": 202, "delete": (409, "reservation_open")}
        assert Transaction.objects.filter(project=project, status=Transaction.Status.RESERVED).count() == 1

        # Its reservation returned again (the ingestion failed once more): now the delete comes first.
        from apps.payments.services import wallet_service

        wallet_service.rollback_reservation(Transaction.objects.get(project=project, status=Transaction.Status.RESERVED).pk)
        answers = self._race(url, None, first="delete")

        assert answers == {"delete": (409, "balance_left"), "request": 202}

    def _while_a_purchase_holds_the_workspace(self, url):
        """
        A token purchase holds the workspace's row (lock_workspace: FOR NO KEY UPDATE)
        while ``url`` is posted. The answer, or None if it was still waiting after five seconds.
        """
        from apps.payments.services.payments import lock_workspace

        locked, release = threading.Event(), threading.Event()

        def purchase():
            try:
                with transaction.atomic():
                    lock_workspace(self.workspace.pk)
                    locked.set()
                    release.wait(timeout=20)
            finally:
                connection.close()

        answers = {}

        def post():
            try:
                answers["request"] = self._api().post(url, {}, format="json").status_code
            except Exception as exc:
                answers["request"] = type(exc).__name__
            finally:
                connection.close()

        holder = threading.Thread(target=purchase)
        holder.start()
        assert locked.wait(timeout=10)
        request = threading.Thread(target=post)
        request.start()
        request.join(timeout=5)
        answered = answers.get("request")
        release.set()
        for thread in (request, holder):
            thread.join(timeout=30)
        return answered

    def test_a_paid_render_and_a_redispatch_do_not_lock_the_workspace_row(self):
        """
        Only the project's row: with select_related('workspace') and no ``of``, the
        FOR UPDATE took the workspace's too and waited for a purchase of the same workspace.
        """
        _project, clip = self._project(status=VideoProject.Status.COMPLETED, pipeline_stage="completed")
        failed, _clip = self._failed_and_refunded()

        assert self._while_a_purchase_holds_the_workspace(f"/api/v1/clips/{clip.pk}/render/") == 202
        assert self._while_a_purchase_holds_the_workspace(f"/api/v1/projects/{failed.pk}/redispatch/") == 202
