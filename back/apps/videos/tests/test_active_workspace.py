"""
Every request acts in one workspace: the one in ``X-Workspace-Id`` (what the
web app sends), or, without the header, the oldest one the user owns, else
their oldest ACTIVE membership. Only an ACTIVE membership or owning it gives
access: a pending invitation gets the same 404 as a workspace that does not
exist. The role there decides what the request can do (table 1.7 of the
screen map; front/src/lib/permissions.ts).

Some workspaces get the lowest or the highest possible id: the old
``user.workspaces.first()`` ordered by id, so these tests fail with it every
time, not by chance.
"""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate
from rest_framework.views import APIView

from apps.core.permissions import ADMINS, DENIED, EDITOR_DELETE_DENIED, MAKERS, WorkspaceRolePermission
from apps.core.workspace import NOT_FOUND_DETAIL, WORKSPACE_HEADER
from apps.payments.models import Transaction, Wallet
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import BrandKit, ClipPreview, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import CLIP_DELAY, DELAY, REDIS_CLIENT

User = get_user_model()

LOWEST_ID = uuid.UUID(int=1)
HIGHEST_ID = uuid.UUID(int=(1 << 128) - 1)
INGESTION = "apps.videos.views.process_initial_ingestion.delay"
PREVIEW_TASK = "apps.videos.tasks.render_clip_preview_task.delay"
LINK = "https://www.youtube.com/watch?v=x"
LINK_COST = Decimal("10")

ACTIVE, PENDING = WorkspaceMember.Status.ACTIVE, WorkspaceMember.Status.PENDING
ADMIN, EDITOR, VIEWER = WorkspaceMember.Role.ADMIN, WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER

PROJECTS = reverse("project-list")
BRAND_KITS = reverse("brandkit-list")
FROM_PROMPT = reverse("project-from-prompt")


def detail(project):
    return reverse("project-detail", args=[project.pk])


def paper_edit(project):
    return reverse("project-paper-edit-data", args=[project.pk])


def approve(project):
    return reverse("project-approve-paper-edit", args=[project.pk])


def preview(clip):
    return reverse("clip-preview", args=[clip.pk])


def kit_detail(kit):
    return reverse("brandkit-detail", args=[kit.pk])


def header(workspace):
    return {"HTTP_X_WORKSPACE_ID": str(workspace if isinstance(workspace, (str, uuid.UUID)) else workspace.pk)}


def make_user(name):
    return User.objects.create_user(username=name, email=f"{name}@test.com", password="pw")


def home_of(user):
    """The personal workspace the post_save signal of User creates."""
    return user.owned_workspaces.get()


def team(owner, name="Equipo", pk=HIGHEST_ID):
    return Workspace.objects.create(id=pk, name=name, owner=owner)


def join(workspace, user, role, status=ACTIVE):
    return WorkspaceMember.objects.create(workspace=workspace, user=user, role=role, status=status)


def fund(workspace, amount="100.00"):
    wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
    Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal(amount), reserved_balance=0)
    return wallet


def balances(workspace):
    wallet = Wallet.objects.get(workspace=workspace)
    return wallet.available_balance, wallet.reserved_balance


def project_in(workspace, user, title, **fields):
    return VideoProject.objects.create(
        workspace=workspace, uploaded_by=user, title=title, source_file="test/path/file.mp4", **fields,
    )


def in_review(workspace, user, title="En revisión"):
    """A project where the ingestion leaves it for the review, with one suggested clip."""
    project = project_in(
        workspace, user, title, original_r2_key="videos/1/original/source.mp4",
        status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED,
        add_subtitles=False,
    )
    VideoClip.objects.create(project=project, title="Sugerido", start_time=0, end_time=5)
    return project


class ActiveWorkspaceHarness(APITestCase):
    def setUp(self):
        self.redis = FakeRedis()
        redis_patcher = patch(REDIS_CLIENT, return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)
        self.alice = make_user("alice")
        self.home = home_of(self.alice)
        self.boss = make_user("boss")
        self.team = team(self.boss)
        fund(self.home)
        fund(self.team)
        self.client.force_authenticate(user=self.alice)

    def create(self, workspace=None, url=PROJECTS, **payload):
        body = {"title": "Nuevo", "video_url": LINK, **payload}
        extra = header(workspace) if workspace is not None else {}
        with patch(INGESTION):
            return self.client.post(url, body, format="json", **extra)

    def approve(self, project, workspace):
        segments = [{"start": 0.0, "end": 5.0}]
        with patch(CLIP_DELAY), patch(DELAY), self.captureOnCommitCallbacks(execute=False):
            return self.client.post(approve(project), {"approved_segments": segments}, format="json", **header(workspace))


class HeaderPicksTheWorkspaceTests(ActiveWorkspaceHarness):
    def setUp(self):
        super().setUp()
        join(self.team, self.alice, EDITOR)

    def test_the_project_is_created_and_charged_in_the_header_workspace(self):
        for workspace, other in ((self.team, self.home), (self.home, self.team)):
            with self.subTest(workspace=workspace.name):
                before = balances(other)
                response = self.create(workspace)

                self.assertEqual(response.status_code, 201, response.content[:300])
                project = VideoProject.objects.get(pk=response.data["id"])
                self.assertEqual(project.workspace, workspace)
                reservation = Transaction.objects.get(pk=project.metadata["reservation_id"])
                self.assertEqual(reservation.wallet.workspace, workspace)
                self.assertEqual(balances(workspace)[1], LINK_COST)
                self.assertEqual(balances(other), before)
                fund(workspace)

    def test_from_prompt_is_created_and_charged_in_the_header_workspace(self):
        response = self.create(self.team, url=FROM_PROMPT, prompt="clips cortos")

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.workspace, self.team)
        self.assertEqual(balances(self.team)[1], LINK_COST)
        self.assertEqual(balances(self.home), (Decimal("100"), Decimal("0")))

    def test_a_brand_kit_is_created_in_the_header_workspace(self):
        response = self.client.post(BRAND_KITS, {"name": "Kit del equipo"}, format="json", **header(self.team))

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(BrandKit.objects.get(pk=response.data["id"]).workspace, self.team)

    def test_a_project_cannot_use_a_brand_kit_of_another_workspace(self):
        theirs = BrandKit.objects.create(workspace=self.team, name="Del equipo")

        response = self.create(self.home, brand_kit=str(theirs.pk))

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("brand_kit", response.data)
        self.assertFalse(VideoProject.objects.exists())
        self.assertEqual(self.create(self.team, brand_kit=str(theirs.pk)).status_code, 201)


    def test_an_update_cannot_use_a_brand_kit_of_another_workspace(self):
        project = project_in(self.home, self.alice, "Mío")
        theirs = BrandKit.objects.create(workspace=self.team, name="Del equipo")
        mine = BrandKit.objects.create(workspace=self.home, name="Mío")
        own = header(self.home)
        url = detail(project)
        for method, fmt in (("patch", "json"), ("put", "json"), ("put", "multipart")):
            with self.subTest(method=method, format=fmt):
                response = getattr(self.client, method)(
                    url, {"title": "Mío", "brand_kit": str(theirs.pk)}, format=fmt, **own,
                )
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertIn("brand_kit", response.data)
                project.refresh_from_db()
                self.assertIsNone(project.brand_kit_id)

        response = self.client.patch(url, {"brand_kit": str(mine.pk)}, format="json", **own)
        self.assertEqual(response.status_code, 200, response.content[:300])
        project.refresh_from_db()
        self.assertEqual(project.brand_kit, mine)


class ListsFollowTheHeaderTests(ActiveWorkspaceHarness):
    def setUp(self):
        super().setUp()
        join(self.team, self.alice, EDITOR)
        project_in(self.home, self.alice, "Mío")
        project_in(self.team, self.boss, "Del equipo")
        BrandKit.objects.create(workspace=self.home, name="Kit mío")
        BrandKit.objects.create(workspace=self.team, name="Kit del equipo")

    def titles(self, url, workspace, key="title"):
        response = self.client.get(url, **header(workspace))
        self.assertEqual(response.status_code, 200, response.content[:300])
        return [item[key] for item in response.data]

    def test_projects_of_the_header_workspace_only(self):
        self.assertEqual(self.titles(PROJECTS, self.home), ["Mío"])
        self.assertEqual(self.titles(PROJECTS, self.team), ["Del equipo"])

    def test_brand_kits_of_the_header_workspace_only(self):
        self.assertEqual(self.titles(BRAND_KITS, self.home, "name"), ["Kit mío"])
        self.assertEqual(self.titles(BRAND_KITS, self.team, "name"), ["Kit del equipo"])


class ObjectsOfAnotherWorkspaceTests(ActiveWorkspaceHarness):
    """Alice is admin in both workspaces: the header, not her role, hides the project."""

    def setUp(self):
        super().setUp()
        join(self.team, self.alice, ADMIN)
        self.project = in_review(self.home, self.alice, "Mío")
        self.clip = self.project.clips.get()
        self.kit = BrandKit.objects.create(workspace=self.home, name="Kit mío")

    def test_retrieve_update_delete_and_actions_are_404(self):
        other = header(self.team)
        with patch(PREVIEW_TASK):
            responses = {
                "retrieve": self.client.get(detail(self.project), **other),
                "patch": self.client.patch(detail(self.project), {"title": "Cambiado"}, format="json", **other),
                "put": self.client.put(detail(self.project), {"title": "Cambiado", "video_url": LINK}, format="json", **other),
                "delete": self.client.delete(detail(self.project), **other),
                "paper-edit-data": self.client.get(paper_edit(self.project), **other),
                "approve": self.approve(self.project, self.team),
                "preview get": self.client.get(preview(self.clip), **other),
                "preview post": self.client.post(preview(self.clip), **other),
                "brand kit": self.client.get(kit_detail(self.kit), **other),
                "brand kit patch": self.client.patch(kit_detail(self.kit), {"name": "X"}, format="json", **other),
            }
        for name, response in responses.items():
            with self.subTest(name):
                self.assertEqual(response.status_code, 404, response.content[:300])

        self.project.refresh_from_db()
        self.assertEqual(self.project.title, "Mío")
        self.assertEqual(self.project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertFalse(ClipPreview.objects.exists())
        self.assertEqual(balances(self.home), (Decimal("100"), Decimal("0")))
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.name, "Kit mío")

    def test_with_its_own_header_the_same_requests_work(self):
        own = header(self.home)
        self.assertEqual(self.client.get(detail(self.project), **own).status_code, 200)
        self.assertEqual(self.client.get(paper_edit(self.project), **own).status_code, 200)
        self.assertEqual(self.approve(self.project, self.home).status_code, 200)


class ApprovalChargesTheProjectWorkspaceTests(ActiveWorkspaceHarness):
    def test_the_approval_is_reserved_in_the_wallet_of_the_project_workspace(self):
        """Alice's default workspace is her own; the team's project is paid by the team."""
        join(self.team, self.alice, EDITOR)
        project = in_review(self.team, self.boss)

        response = self.approve(project, self.team)

        self.assertEqual(response.status_code, 200, response.content[:300])
        cost = Decimal(str(response.data["final_cost"]))
        self.assertGreater(cost, 0)
        self.assertEqual(balances(self.team), (Decimal("100") - cost, cost))
        self.assertEqual(Transaction.objects.get(pk=response.data["transaction_id"]).wallet.workspace, self.team)
        self.assertEqual(balances(self.home), (Decimal("100"), Decimal("0")))


class PendingInvitationTests(ActiveWorkspaceHarness):
    def setUp(self):
        super().setUp()
        self.invitation = join(self.team, self.alice, EDITOR, status=PENDING)
        self.project = project_in(self.team, self.boss, "Del equipo")
        self.clip = VideoClip.objects.create(project=self.project, title="Clip", start_time=0, end_time=5)

    def test_a_pending_invitation_has_no_access(self):
        invited = header(self.team)
        with patch(PREVIEW_TASK):
            responses = {
                "list": self.client.get(PROJECTS, **invited),
                "retrieve": self.client.get(detail(self.project), **invited),
                "patch": self.client.patch(detail(self.project), {"title": "Cambiado"}, format="json", **invited),
                "create": self.create(self.team),
                "brand kits": self.client.get(BRAND_KITS, **invited),
                "preview": self.client.post(preview(self.clip), **invited),
            }
        for name, response in responses.items():
            with self.subTest(name):
                self.assertEqual(response.status_code, 404, response.content[:300])
                self.assertEqual(response.data, {"detail": NOT_FOUND_DETAIL})

        self.assertEqual(VideoProject.objects.filter(workspace=self.team).count(), 1)
        self.assertEqual(balances(self.team), (Decimal("100"), Decimal("0")))

    def test_the_answer_is_the_one_of_a_workspace_that_does_not_exist(self):
        invited = self.client.get(PROJECTS, **header(self.team))
        missing = self.client.get(PROJECTS, **header(uuid.uuid4()))
        malformed = self.client.get(PROJECTS, **header("no-es-un-id"))
        stranger = self.client.get(PROJECTS, **header(team(self.boss, "Ajeno", pk=uuid.uuid4())))

        for response in (invited, missing, malformed, stranger):
            self.assertEqual((response.status_code, response.data), (404, {"detail": NOT_FOUND_DETAIL}))

    def test_without_the_header_the_invitation_is_not_the_default(self):
        response = self.client.get(PROJECTS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_accepting_the_invitation_gives_access(self):
        self.invitation.status = ACTIVE
        self.invitation.save(update_fields=["status"])

        response = self.client.get(PROJECTS, **header(self.team))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["title"] for item in response.data], ["Del equipo"])


class ViewerTests(ActiveWorkspaceHarness):
    """Lector: sees projects, clips and brand kits, and the free preview; changes nothing."""

    def setUp(self):
        super().setUp()
        join(self.team, self.alice, VIEWER)
        self.project = in_review(self.team, self.boss)
        self.clip = self.project.clips.get()
        self.kit = BrandKit.objects.create(workspace=self.team, name="Kit")

    def test_reads_are_200(self):
        viewer = header(self.team)
        for name, url in (
            ("list", PROJECTS), ("retrieve", detail(self.project)), ("paper-edit-data", paper_edit(self.project)),
            ("brand kits", BRAND_KITS), ("brand kit", kit_detail(self.kit)),
        ):
            with self.subTest(name):
                self.assertEqual(self.client.get(url, **viewer).status_code, 200)

    def test_the_free_preview_is_allowed(self):
        with patch(PREVIEW_TASK):
            response = self.client.post(preview(self.clip), **header(self.team))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(self.client.get(preview(self.clip), **header(self.team)).status_code, 200)

    def test_writes_approve_and_render_are_403(self):
        viewer = header(self.team)
        responses = {
            "create": self.create(self.team),
            "from-prompt": self.create(self.team, url=FROM_PROMPT, prompt="clips cortos"),
            "render without review": self.create(self.team, auto_render_bypass=True),
            "patch": self.client.patch(detail(self.project), {"title": "Cambiado"}, format="json", **viewer),
            "put": self.client.put(detail(self.project), {"title": "Cambiado", "video_url": LINK}, format="json", **viewer),
            "delete": self.client.delete(detail(self.project), **viewer),
            "approve": self.approve(self.project, self.team),
            "brand kit create": self.client.post(BRAND_KITS, {"name": "Otro"}, format="json", **viewer),
            "brand kit patch": self.client.patch(kit_detail(self.kit), {"name": "X"}, format="json", **viewer),
            "brand kit delete": self.client.delete(kit_detail(self.kit), **viewer),
        }
        for name, response in responses.items():
            with self.subTest(name):
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertEqual(response.data, {"detail": DENIED[MAKERS]})

        self.assertEqual(list(VideoProject.objects.filter(workspace=self.team)), [self.project])
        self.project.refresh_from_db()
        self.assertEqual((self.project.title, self.project.status), ("En revisión", VideoProject.Status.AWAITING_APPROVAL))
        self.assertEqual(balances(self.team), (Decimal("100"), Decimal("0")))
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.name, "Kit")

    def test_the_role_is_the_one_of_the_header_workspace(self):
        """Owner of her own workspace, Lector in the team: she can create in hers."""
        self.assertEqual(self.create(self.home).status_code, 201)
        self.assertEqual(self.create(self.team).status_code, 403)


class EditorTests(ActiveWorkspaceHarness):
    def setUp(self):
        super().setUp()
        join(self.team, self.alice, EDITOR)
        self.theirs = in_review(self.team, self.boss, "Del jefe")

    def test_an_editor_creates_edits_approves_and_manages_brand_kits(self):
        editor = header(self.team)
        created = self.create(self.team)
        self.assertEqual(created.status_code, 201, created.content[:300])
        self.assertEqual(VideoProject.objects.get(pk=created.data["id"]).workspace, self.team)

        renamed = self.client.patch(detail(self.theirs), {"title": "Renombrado"}, format="json", **editor)
        self.assertEqual(renamed.status_code, 200, renamed.content[:300])
        self.assertEqual(self.approve(self.theirs, self.team).status_code, 200)

        kit = self.client.post(BRAND_KITS, {"name": "Kit"}, format="json", **editor)
        self.assertEqual(kit.status_code, 201, kit.content[:300])
        kit_url = reverse("brandkit-detail", args=[kit.data["id"]])
        self.assertEqual(self.client.patch(kit_url, {"name": "Otro"}, format="json", **editor).status_code, 200)
        self.assertEqual(self.client.delete(kit_url, **editor).status_code, 204)

    def test_an_editor_deletes_only_the_projects_they_uploaded(self):
        editor = header(self.team)
        mine = project_in(self.team, self.alice, "Mío")

        refused = self.client.delete(detail(self.theirs), **editor)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.data, {"detail": EDITOR_DELETE_DENIED})
        self.assertTrue(VideoProject.objects.filter(pk=self.theirs.pk).exists())

        self.assertEqual(self.client.delete(detail(mine), **editor).status_code, 204)

    def test_an_admin_and_the_owner_delete_any_project(self):
        WorkspaceMember.objects.filter(workspace=self.team, user=self.alice).update(role=ADMIN)
        self.assertEqual(self.client.delete(detail(self.theirs), **header(self.team)).status_code, 204)

        # The owner wins over a membership row of their own, even a pending Lector one.
        join(self.team, self.boss, VIEWER, status=PENDING)
        self.client.force_authenticate(user=self.boss)
        alices = project_in(self.team, self.alice, "De Alice")
        self.assertEqual(self.client.delete(detail(alices), **header(self.team)).status_code, 204)


    def test_a_project_nobody_uploaded_is_deleted_by_an_admin_or_the_owner_only(self):
        orphans = [project_in(self.team, None, f"Sin autor {n}") for n in range(2)]

        refused = self.client.delete(detail(orphans[0]), **header(self.team))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.data, {"detail": EDITOR_DELETE_DENIED})

        WorkspaceMember.objects.filter(workspace=self.team, user=self.alice).update(role=ADMIN)
        self.assertEqual(self.client.delete(detail(orphans[0]), **header(self.team)).status_code, 204)
        self.client.force_authenticate(user=self.boss)
        self.assertEqual(self.client.delete(detail(orphans[1]), **header(self.team)).status_code, 204)


class MethodNotAllowedTests(ActiveWorkspaceHarness):
    def test_a_method_the_view_does_not_have_is_405_for_every_role(self):
        project = project_in(self.team, self.boss, "Del equipo")
        clip = VideoClip.objects.create(project=project, title="Clip", start_time=0, end_time=5)
        membership = join(self.team, self.alice, VIEWER)
        for role in (VIEWER, EDITOR, ADMIN):
            membership.role = role
            membership.save(update_fields=["role"])
            for name, response in (
                ("patch preview", self.client.patch(preview(clip), {}, format="json", **header(self.team))),
                ("put list", self.client.put(PROJECTS, {}, format="json", **header(self.team))),
                ("post paper-edit-data", self.client.post(paper_edit(project), {}, format="json", **header(self.team))),
            ):
                with self.subTest(role=role, request=name):
                    self.assertEqual(response.status_code, 405, response.content[:300])


class _UnmappedView(APIView):
    """A scoped view that maps none of its methods to an action."""

    permission_classes = [IsAuthenticated, WorkspaceRolePermission]

    def get(self, request):
        return Response({"ok": True})

    def post(self, request):
        return Response({"ok": True})


class UnmappedWriteTests(ActiveWorkspaceHarness):
    def call(self, method, user):
        request = getattr(APIRequestFactory(), method)("/", **header(self.team))
        force_authenticate(request, user=user)
        return _UnmappedView.as_view()(request)

    def test_an_unmapped_write_is_for_the_owner_and_admins_only(self):
        members = {role: make_user(f"m-{role}") for role in (ADMIN, EDITOR, VIEWER)}
        for role, user in members.items():
            join(self.team, user, role)
        expected = {self.boss: 200, members[ADMIN]: 200, members[EDITOR]: 403, members[VIEWER]: 403}
        for user, status_code in expected.items():
            with self.subTest(user=user.username):
                response = self.call("post", user)
                self.assertEqual(response.status_code, status_code)
                if status_code == 403:
                    self.assertEqual(response.data, {"detail": DENIED[ADMINS]})
                # A read left out of the map is for every role.
                self.assertEqual(self.call("get", user).status_code, 200)


class WithoutTheHeaderTests(ActiveWorkspaceHarness):
    def test_the_default_is_the_workspace_the_user_owns(self):
        lowest = team(self.boss, "Primero por id", pk=LOWEST_ID)
        fund(lowest)
        join(lowest, self.alice, ADMIN)
        project_in(self.home, self.alice, "Mío")
        project_in(lowest, self.boss, "Del otro")

        response = self.create()

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(VideoProject.objects.get(pk=response.data["id"]).workspace, self.home)
        self.assertEqual(balances(self.home)[1], LINK_COST)
        self.assertEqual(balances(lowest), (Decimal("100"), Decimal("0")))
        self.assertEqual(
            sorted(item["title"] for item in self.client.get(PROJECTS).data), ["Mío", "Nuevo"],
        )
        # A blank header is no header.
        self.assertEqual(self.client.get(PROJECTS, HTTP_X_WORKSPACE_ID=" ").status_code, 200)

    def test_without_a_workspace_of_their_own_the_oldest_active_membership(self):
        self.home.delete()
        invited = team(self.boss, "Invitación", pk=LOWEST_ID)
        join(invited, self.alice, ADMIN, status=PENDING)
        first = team(self.boss, "Primero", pk=uuid.uuid4())
        fund(first)
        join(first, self.alice, EDITOR)
        join(self.team, self.alice, ADMIN)

        response = self.create()

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(VideoProject.objects.get(pk=response.data["id"]).workspace, first)
        self.assertEqual(balances(first)[1], LINK_COST)

    def test_without_any_workspace_creating_is_400_and_lists_are_empty(self):
        self.home.delete()
        join(self.team, self.alice, EDITOR, status=PENDING)
        project_in(self.team, self.boss, "Del equipo")

        self.assertEqual(self.client.get(PROJECTS).data, [])
        response = self.create()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"error": "El usuario no tiene un Workspace asignado."})


    def test_without_any_workspace_creating_a_brand_kit_is_400(self):
        self.home.delete()
        join(self.team, self.alice, EDITOR, status=PENDING)

        response = self.client.post(BRAND_KITS, {"name": "Kit"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"error": "El usuario no tiene un Workspace asignado."})
        self.assertFalse(BrandKit.objects.exists())


class SchemaDeclaresTheHeaderTests(APITestCase):
    def test_the_scoped_operations_declare_x_workspace_id(self):
        paths = SchemaGenerator().get_schema(request=None, public=True)["paths"]

        def headers(path, method):
            return [p["name"] for p in paths[path][method].get("parameters", []) if p["in"] == "header"]

        for path, method in (
            ("/api/v1/projects/", "get"), ("/api/v1/projects/", "post"), ("/api/v1/projects/{id}/", "patch"),
            ("/api/v1/projects/{id}/approve-paper-edit/", "post"), ("/api/v1/brand-kits/", "post"),
            ("/api/v1/clips/{clip_id}/preview/", "post"),
        ):
            with self.subTest(path=path, method=method):
                self.assertEqual(headers(path, method), [WORKSPACE_HEADER])
        self.assertEqual(headers("/api/token/", "post"), [])


class CorsAllowsTheHeaderTests(APITestCase):
    @override_settings(CORS_ALLOW_ALL_ORIGINS=True)
    def test_the_preflight_allows_x_workspace_id(self):
        response = self.client.options(
            PROJECTS,
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization, x-workspace-id",
        )

        allowed = {name.strip() for name in response["access-control-allow-headers"].split(",")}
        self.assertIn("x-workspace-id", allowed)
