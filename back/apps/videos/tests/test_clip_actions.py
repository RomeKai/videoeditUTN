"""
Clip and render actions of apps/videos/views_clips.py (gap 11 of the screen
map; front/src/api/contracts/clips.ts and projects.ts): the clip itself (GET,
PATCH, DELETE), its paid render from the editor, the retry of a failed clip,
the paid rerender of every clip and the re-dispatch of a failed pipeline.

The money rules: a paid render reserves before anything changes and is
settled by the seconds delivered (CLIP_RENDER in tasks/ingestion.py), only
one render reservation of a project is open at a time, and a lost message is
brought back by recover_stuck_renders or settled by settle_stale_reservations.
"""

import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.payments.services.wallet_service import commit_partial_reservation, reserve_funds, rollback_reservation
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import BrandKit, ClipPreview, ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import render_clip_task
from apps.videos.tasks.ingestion import (
    CLIP_RENDER, _reserve_auto_render, _run_ingestion, _whole_video_render_allowed,
)
from apps.videos.tasks.render_recovery import recover_stuck_renders_now
from apps.videos.tests.fakes import CapturedTaskFailures, FakeRedis
from apps.videos.tests.test_approve_per_clip import RENDER_CLIP, RESUME, fake_render
from apps.videos.tests.test_approved_segments_source import CLIP_DELAY, REDIS_CLIENT
from apps.videos.tests.test_ingestion_pipeline import fund_wallet

User = get_user_model()

DONE, FAILED, DRAFT, RENDERING = (
    VideoClip.Status.COMPLETED, VideoClip.Status.FAILED, VideoClip.Status.DRAFT, VideoClip.Status.RENDERING,
)
INGESTION_DELAY = "apps.videos.tasks.ingestion.process_initial_ingestion.delay"
DELETE_OBJECT = "apps.videos.services.storage_service.CloudflareR2Manager.delete_object"
RESERVED, CHARGED, REFUNDED = Transaction.Status.RESERVED, Transaction.Status.COMPLETED, Transaction.Status.FAILED


def clip_url(clip, action=None):
    return reverse("clip-detail" if action is None else f"clip-{action}", args=[clip.pk])


def project_url(project, action):
    return reverse(f"project-{action}", args=[project.pk])


class ClipActionsHarness:
    """A workspace with tokens and a finished project, as the review leaves one after its render."""

    def setUp(self):
        super().setUp()
        self.redis = FakeRedis()
        redis_patcher = patch(REDIS_CLIENT, return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)
        self.user = User.objects.create_user(username="dueno", email="dueno@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Clips", owner=self.user)
        self.wallet = fund_wallet(self.workspace, "100.00")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))

    def finished(self, statuses=(DONE, FAILED), **fields):
        """
        An approved review rendered clip by clip: 10 s clips at 0, 20, 40...,
        delivered or failed as ``statuses``, the approval settled by what was
        delivered. The wallet is put back to 100 / 0 after.
        """
        stage = PipelineStage.PARTIAL if FAILED in statuses else PipelineStage.COMPLETED
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Entregado",
            source_file="videos/raw/source.mp4", original_r2_key="videos/1/original/source.mp4",
            status=stage, pipeline_stage=stage, add_subtitles=False,
            metadata={"duration": 120.0}, **fields,
        )
        clips = [
            VideoClip.objects.create(
                project=project, title=f"Clip {number}", start_time=number * 20, end_time=number * 20 + 10,
                status=status, s3_object_key=f"videos/old-{number}.mp4" if status == DONE else None,
            )
            for number, status in enumerate(statuses)
        ]
        approval = reserve_funds(self.wallet.id, Decimal("5.00") * len(clips))
        commit_partial_reservation(approval.id, Decimal("5.00") * statuses.count(DONE))
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **project.metadata,
            "render_reservation_id": str(approval.id),
            "render_clip_ids": [str(clip.pk) for clip in clips],
            "render_seconds": 10.0 * len(clips),
        })
        fund_wallet(self.workspace, "100.00")
        project.refresh_from_db()
        return project, clips

    def whole_video(self, clip_status=DONE, stage=PipelineStage.COMPLETED, creation=CHARGED):
        """A single_edit priced for its 30 s at creation, delivered and charged (or as asked)."""
        status = {PipelineStage.COMPLETED: VideoProject.Status.COMPLETED, PipelineStage.PARTIAL: VideoProject.Status.PARTIAL,
                  PipelineStage.FAILED: VideoProject.Status.FAILED}[stage]
        creation_txn = reserve_funds(self.wallet.id, Decimal("15.00"))
        if creation == CHARGED:
            commit_partial_reservation(creation_txn.id, Decimal("15.00"))
        elif creation == REFUNDED:
            rollback_reservation(creation_txn.id)
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Video completo",
            source_file="videos/raw/whole.mp4", project_type=VideoProject.Type.SINGLE_EDIT,
            status=status, pipeline_stage=stage, add_subtitles=False,
            metadata={"priced_duration_s": 30, "duration": 30.0, "reservation_id": str(creation_txn.id)},
        )
        clip = None
        if clip_status is not None:
            clip = VideoClip.objects.create(
                project=project, title="Video completo", start_time=0, end_time=30, status=clip_status,
                s3_object_key="videos/whole-old.mp4" if clip_status == DONE else None,
            )
        fund_wallet(self.workspace, "100.00")
        self.creation = creation_txn
        return project, clip

    def post(self, url, data=None, execute=True):
        """Posts; returns (response, render_clip_task.delay)."""
        with patch(CLIP_DELAY) as delay, self.captureOnCommitCallbacks(execute=execute):
            response = self.client.post(url, data or {}, format="json")
        return response, delay

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def clip_render(self, project):
        project.refresh_from_db()
        return Transaction.objects.get(pk=project.metadata[CLIP_RENDER.reservation_key])

    def render_with(self, clip, side_effect=fake_render):
        with patch(RENDER_CLIP, side_effect=side_effect):
            return render_clip_task.apply(args=[str(clip.pk)])

    def member(self, role, name):
        user = User.objects.create_user(username=name, email=f"{name}@test.com", password="pw")
        WorkspaceMember.objects.create(
            workspace=self.workspace, user=user, role=role, status=WorkspaceMember.Status.ACTIVE,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        return client


# --- The clip ------------------------------------------------------------------

class ClipDetailTests(ClipActionsHarness, APITestCase):
    def test_a_clip_is_read_as_the_project_sends_it(self):
        _project, (clip, _failed) = self.finished()

        response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.data["id"], str(clip.pk))
        self.assertEqual(response.data["status"], DONE)
        self.assertNotIn("render_claim_token", response.data)
        self.assertEqual(response.data["layers"], [])

    def test_the_title_and_the_trim_change_and_the_old_previews_go(self):
        _project, (clip, _failed) = self.finished()
        ClipPreview.objects.create(clip=clip, expires_at=timezone.now() + timedelta(hours=1), storage_key="previews/a.mp4")

        with patch(DELETE_OBJECT) as delete, self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                clip_url(clip), {"title": "  Nuevo título ", "start_time": 2, "end_time": 9.5}, format="json",
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        clip.refresh_from_db()
        self.assertEqual((clip.title, clip.start_time, clip.end_time), ("Nuevo título", 2.0, 9.5))
        self.assertEqual(clip.status, DONE)  # saving never renders
        self.assertFalse(ClipPreview.objects.filter(clip=clip).exists())
        delete.assert_called_once_with("previews/a.mp4")
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())

    def test_only_the_title_keeps_the_previews(self):
        _project, (clip, _failed) = self.finished()
        ClipPreview.objects.create(clip=clip, expires_at=timezone.now() + timedelta(hours=1))

        response = self.client.patch(clip_url(clip), {"title": "Otro"}, format="json")

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(ClipPreview.objects.filter(clip=clip).exists())

    def test_an_invalid_trim_is_a_400_on_the_field(self):
        _project, (clip, _failed) = self.finished()
        cases = [
            ({"start_time": 8, "end_time": 8}, "end_time", "El fin tiene que ser mayor que el inicio."),
            ({"end_time": 121}, "end_time", "El recorte se pasa del final del video."),
            ({"start_time": -1}, "start_time", "El inicio no puede ser negativo."),
            ({"title": ""}, "title", "Poné un título para el clip."),
            ({"end_time": "mucho"}, "end_time", "Tiene que ser un número."),
        ]
        for body, field, message in cases:
            with self.subTest(body=body):
                response = self.client.patch(clip_url(clip), body, format="json")
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertEqual(response.data[field], [message])
        clip.refresh_from_db()
        self.assertEqual((clip.title, clip.start_time, clip.end_time), ("Clip 0", 0.0, 10.0))

    @override_settings(RENDER_CLIP_SOFT_TIME_LIMIT=40, RENDER_CLIP_SECONDS_PER_SECOND=4)
    def test_a_trim_longer_than_a_render_allows_is_refused(self):
        _project, (clip, _failed) = self.finished()

        response = self.client.patch(clip_url(clip), {"end_time": 10.5}, format="json")

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.data["end_time"], ["El clip puede durar hasta 10 segundos."])

    def test_while_the_project_renders_only_the_title_changes(self):
        project, (clip, _failed) = self.finished()
        VideoProject.objects.filter(pk=project.pk).update(status=VideoProject.Status.RENDERING)

        trim = self.client.patch(clip_url(clip), {"start_time": 1}, format="json")
        title = self.client.patch(clip_url(clip), {"title": "Mientras renderiza"}, format="json")

        self.assertEqual(trim.status_code, 409, trim.content[:300])
        self.assertEqual(trim.data["code"], "project_rendering")
        self.assertEqual(title.status_code, 200, title.content[:300])
        clip.refresh_from_db()
        self.assertEqual((clip.title, clip.start_time), ("Mientras renderiza", 0.0))

    def test_while_the_pipeline_processes_the_trim_cannot_change(self):
        project, (clip, _failed) = self.finished()
        VideoProject.objects.filter(pk=project.pk).update(status=VideoProject.Status.INGESTING)

        response = self.client.patch(clip_url(clip), {"end_time": 9}, format="json")

        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertEqual(response.data["code"], "project_processing")

    def test_a_clip_in_review_can_be_trimmed(self):
        project, (clip,) = self.finished(statuses=(DONE,))
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED, metadata={},
        )
        VideoClip.objects.filter(pk=clip.pk).update(status=DRAFT)

        response = self.client.patch(clip_url(clip), {"end_time": 8}, format="json")

        self.assertEqual(response.status_code, 200, response.content[:300])

    def test_delete_takes_the_clip_its_posts_and_its_files(self):
        _project, (clip, _failed) = self.finished()
        ScheduledPost.objects.create(video_clip=clip, platform="TIKTOK", publish_at=timezone.now() + timedelta(days=1),
                                     status=ScheduledPost.Status.SCHEDULED)
        ClipPreview.objects.create(clip=clip, expires_at=timezone.now() + timedelta(hours=1), storage_key="previews/b.mp4")

        with patch(DELETE_OBJECT) as delete, self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(clip_url(clip))

        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertFalse(VideoClip.objects.filter(pk=clip.pk).exists())
        self.assertFalse(ScheduledPost.objects.filter(video_clip_id=clip.pk).exists())
        self.assertEqual(sorted(call.args[0] for call in delete.call_args_list), ["previews/b.mp4", "videos/old-0.mp4"])

    def test_delete_during_a_render_is_refused(self):
        project, (clip, failed) = self.finished()
        self.post(clip_url(failed, "retry"))  # the project renders the failed clip again

        response = self.client.delete(clip_url(clip))
        rendering = self.client.delete(clip_url(failed))

        self.assertEqual((response.status_code, rendering.status_code), (409, 409))
        self.assertEqual(response.data["code"], "project_rendering")
        self.assertTrue(VideoClip.objects.filter(pk__in=[clip.pk, failed.pk]).count() == 2)
        # The settle still counts the delivered clip it would have removed.
        self.assertEqual(self.clip_render(project).status, RESERVED)

    def test_delete_waits_for_a_post_being_uploaded(self):
        _project, (clip, _failed) = self.finished()
        ScheduledPost.objects.create(video_clip=clip, platform="TIKTOK", publish_at=timezone.now(),
                                     status=ScheduledPost.Status.PROCESSING)

        response = self.client.delete(clip_url(clip))

        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertEqual(response.data["code"], "post_processing")


class RolesAndWorkspaceTests(ClipActionsHarness, APITestCase):
    def test_a_viewer_reads_and_cannot_write_or_spend(self):
        project, (clip, failed) = self.finished()
        viewer = self.member(WorkspaceMember.Role.VIEWER, "lector")

        self.assertEqual(viewer.get(clip_url(clip)).status_code, 200)
        with patch(CLIP_DELAY) as delay, patch(INGESTION_DELAY) as ingestion:
            responses = [
                viewer.patch(clip_url(clip), {"title": "x"}, format="json"),
                viewer.delete(clip_url(clip)),
                viewer.post(clip_url(clip, "render")),
                viewer.post(clip_url(failed, "retry")),
                viewer.post(project_url(project, "rerender"), {}, format="json"),
                viewer.post(project_url(project, "redispatch")),
            ]
        self.assertEqual([response.status_code for response in responses], [403] * 6)
        delay.assert_not_called()
        ingestion.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())
        clip.refresh_from_db()
        self.assertEqual(clip.title, "Clip 0")

    def test_an_editor_renders_but_only_owner_and_admin_redispatch(self):
        project, (clip, _failed) = self.finished()
        # Failed after its clips were selected: it resumes free.
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
            pipeline_stage_status={name: "completed" for name in (
                "audio_extracted", "transcribed", "moderated", "clips_selected")},
        )
        editor = self.member(WorkspaceMember.Role.EDITOR, "editor")
        admin = self.member(WorkspaceMember.Role.ADMIN, "admin")

        with patch(INGESTION_DELAY):
            refused = editor.post(project_url(project, "redispatch"))
            allowed = admin.post(project_url(project, "redispatch"))
        self.assertEqual(refused.status_code, 403, refused.content[:300])
        self.assertEqual(refused.data["detail"], "Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.")
        self.assertEqual(allowed.status_code, 202, allowed.content[:300])

        VideoProject.objects.filter(pk=project.pk).update(status=VideoProject.Status.COMPLETED, pipeline_stage=PipelineStage.COMPLETED)
        with patch(CLIP_DELAY), self.captureOnCommitCallbacks(execute=True):
            rendered = editor.post(clip_url(clip, "render"))
        self.assertEqual(rendered.status_code, 202, rendered.content[:300])

    def test_another_workspace_gets_a_404_for_everything(self):
        _project, (clip, failed) = self.finished()
        stranger = User.objects.create_user(username="otro", email="otro@test.com", password="pw")
        other = Workspace.objects.create(name="Ajeno", owner=stranger)
        fund_wallet(other, "100.00")
        client = APIClient()
        client.force_authenticate(user=stranger)
        client.credentials(HTTP_X_WORKSPACE_ID=str(other.pk))
        project = clip.project

        with patch(CLIP_DELAY) as delay, patch(INGESTION_DELAY):
            responses = [
                client.get(clip_url(clip)),
                client.patch(clip_url(clip), {"title": "x"}, format="json"),
                client.delete(clip_url(clip)),
                client.post(clip_url(clip, "render")),
                client.post(clip_url(failed, "retry")),
                client.post(project_url(project, "rerender"), {}, format="json"),
                client.post(project_url(project, "redispatch")),
            ]
        self.assertEqual([response.status_code for response in responses], [404] * 7)
        delay.assert_not_called()
        self.assertTrue(VideoClip.objects.filter(pk=clip.pk, title="Clip 0").exists())


# --- Paid renders: clips/:id/render/, clips/:id/retry/, projects/:id/rerender/ ---

class ClipRenderTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    def test_it_reserves_reopens_the_project_and_queues_after_the_commit(self):
        project, (clip, _failed) = self.finished()

        with patch(CLIP_DELAY) as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(clip_url(clip, "render"))
            delay.assert_not_called()
            for callback in callbacks:
                callback()

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(response.data["message"], "Render encolado.")
        self.assertEqual(response.data["final_cost"], Decimal("5.00"))  # 10 s at 720p
        delay.assert_called_once_with(str(clip.pk))
        project.refresh_from_db()
        self.assertEqual((project.status, project.pipeline_stage), (VideoProject.Status.RENDERING, PipelineStage.RENDER_DISPATCHED))
        self.assertEqual(project.metadata[CLIP_RENDER.clip_ids_key], [str(clip.pk)])
        self.assertEqual(project.metadata[CLIP_RENDER.seconds_key], 10.0)
        txn = self.clip_render(project)
        self.assertEqual((str(txn.pk), txn.status, txn.amount), (str(response.data["transaction_id"]), RESERVED, Decimal("5.00")))
        clip.refresh_from_db()
        self.assertEqual(clip.status, DRAFT)
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("5.00")))

    def test_a_delivered_render_is_charged_once(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"))

        with patch(DELETE_OBJECT) as delete:
            self.render_with(clip)

        project.refresh_from_db()
        # The approval's failed clip still failed: partial, as before.
        self.assertEqual((project.pipeline_stage, project.status), (PipelineStage.PARTIAL, VideoProject.Status.PARTIAL))
        txn = self.clip_render(project)
        self.assertEqual((txn.status, txn.amount), (CHARGED, Decimal("5.00")))
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("0")))
        delete.assert_called_once_with("videos/old-0.mp4")  # the old render is not orphaned
        # The approval was settled before: never charged again.
        self.assertEqual(Transaction.objects.get(pk=project.metadata["render_reservation_id"]).amount, Decimal("5.00"))

    def test_a_render_that_fails_for_good_is_refunded(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"))

        result = self.render_with(clip, side_effect=RuntimeError("bad encode"))

        self.assertTrue(result.failed())
        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.PARTIAL)
        self.assertEqual(self.clip_render(project).status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))
        clip.refresh_from_db()
        self.assertEqual(clip.status, FAILED)

    def test_without_balance_nothing_changes(self):
        project, (clip, _failed) = self.finished()
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("4.99"))

        response, delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 402, response.content[:300])
        self.assertEqual(response.data, {"error": "Saldo insuficiente para reservar los tokens.", "required": Decimal("5.00")})
        delay.assert_not_called()
        self.assert_untouched(project, clip)

    def assert_untouched(self, project, clip, status=DONE):
        project.refresh_from_db()
        clip.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.PARTIAL)
        self.assertNotIn(CLIP_RENDER.reservation_key, project.metadata)
        self.assertEqual(clip.status, status)
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())

    def test_over_the_plan_it_is_a_403_with_its_code(self):
        project, (clip, _failed) = self.finished()
        plan = SubscriptionPlan.objects.create(name="Chico", max_resolution="720p", max_video_duration_seconds=8)
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=plan)

        duration, _delay = self.post(clip_url(clip, "render"))
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, "render_resolution": "1080p"})
        resolution, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual((duration.status_code, duration.data["code"], duration.data["limit"]), (403, "duration", 8))
        self.assertEqual((resolution.status_code, resolution.data["code"]), (403, "resolution"))
        self.assertEqual(resolution.data["error"], "Tu plan Chico renderiza hasta 720p y este proyecto es 1080p.")
        self.assert_untouched(project, clip)

    @override_settings(RENDER_CLIP_SOFT_TIME_LIMIT=36, RENDER_CLIP_SECONDS_PER_SECOND=4)
    def test_a_clip_too_long_to_render_is_refused_before_reserving(self):
        project, (clip, _failed) = self.finished()

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assert_untouched(project, clip)

    def test_a_clip_of_a_review_renders_with_its_approval(self):
        project, (clip,) = self.finished(statuses=(DONE,))
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED, metadata={},
        )
        VideoClip.objects.filter(pk=clip.pk).update(status=DRAFT)

        response, delay = self.post(clip_url(clip, "render"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "awaiting_approval"))
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())

    def test_while_the_pipeline_processes_it_is_refused(self):
        project, (clip, _failed) = self.finished()
        VideoProject.objects.filter(pk=project.pk).update(status=VideoProject.Status.INGESTING)

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "project_processing"))

    def test_a_second_click_gets_a_409_and_never_a_second_charge(self):
        project, (clip, failed) = self.finished()
        first, _delay = self.post(clip_url(clip, "render"))

        again, delay = self.post(clip_url(clip, "render"))
        other, _delay = self.post(clip_url(failed, "render"))

        self.assertEqual(first.status_code, 202)
        self.assertEqual((again.status_code, again.data["code"]), (409, "clip_rendering"))
        self.assertEqual((other.status_code, other.data["code"]), (409, "project_rendering"))
        delay.assert_not_called()
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_a_leftover_reservation_is_settled_first_by_what_it_had_delivered(self):
        # The approval's settle never ran (a worker died after the close).
        project, (clip, _failed) = self.finished()
        approval = reserve_funds(self.wallet.id, Decimal("10.00"))
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, "render_reservation_id": str(approval.id)})

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        approval.refresh_from_db()
        # Its delivered clip, as it was before this render reset it: charged once.
        self.assertEqual((approval.status, approval.amount), (CHARGED, Decimal("5.00")))
        self.assertEqual(self.clip_render(project).status, RESERVED)

    def test_a_reservation_that_cannot_be_settled_yet_blocks_a_new_one(self):
        project, (clip, _failed) = self.finished()
        approval = reserve_funds(self.wallet.id, Decimal("10.00"))
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, "render_reservation_id": str(approval.id)})

        with patch("apps.videos.tasks.finalize_project_render"):
            response, delay = self.post(clip_url(clip, "render"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "render_unsettled"))
        delay.assert_not_called()
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_a_failed_project_renders_its_clip_and_is_closed_again(self):
        project, (clip, _failed) = self.finished()
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED, pipeline_error_code="OperationalError",
        )

        response, _delay = self.post(clip_url(clip, "render"))
        self.render_with(clip)

        self.assertEqual(response.status_code, 202, response.content[:300])
        project.refresh_from_db()
        self.assertEqual((project.pipeline_stage, project.pipeline_error_code), (PipelineStage.PARTIAL, ""))
        self.assertEqual(self.clip_render(project).status, CHARGED)

    def test_drafts_an_earlier_render_left_never_hold_the_new_one_open(self):
        # A failed project: one approved clip never rendered, its approval settled.
        project, (clip, left) = self.finished(statuses=(DONE, FAILED))
        VideoClip.objects.filter(pk=left.pk).update(status=DRAFT)
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
        )
        self.post(clip_url(clip, "render"))

        with patch(CLIP_DELAY) as delay:
            _run_ingestion(project.pk, attempt=1)  # a resume queues only what this render pays for
        delay.assert_called_once_with(str(clip.pk))
        self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)  # closed, never left open
        self.assertEqual(self.clip_render(project).status, CHARGED)
        left.refresh_from_db()
        self.assertEqual(left.status, DRAFT)  # not failed as unpaid either

    def test_the_trim_cannot_change_under_the_render(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"))

        response = self.client.patch(clip_url(clip), {"end_time": 9}, format="json")

        self.assertEqual((response.status_code, response.data["code"]), (409, "project_rendering"))
        self.assertEqual(self.clip_render(project).amount, Decimal("5.00"))
        clip.refresh_from_db()
        self.assertEqual(clip.end_time, 10.0)


class ClipRetryTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    def test_a_failed_clip_is_paid_once_in_all(self):
        project, (_clip, failed) = self.finished()

        response, delay = self.post(clip_url(failed, "retry"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual((response.data["id"], response.data["status"]), (str(failed.pk), DRAFT))
        self.assertEqual(response.data["final_cost"], Decimal("5.00"))
        delay.assert_called_once_with(str(failed.pk))
        self.render_with(failed)

        project.refresh_from_db()
        self.assertEqual((project.pipeline_stage, project.status), (PipelineStage.COMPLETED, VideoProject.Status.COMPLETED))
        retry = self.clip_render(project)
        self.assertEqual((retry.status, retry.amount), (CHARGED, Decimal("5.00")))
        # The approval charged its delivered half; with the retry, both clips paid once each.
        approval = Transaction.objects.get(pk=project.metadata["render_reservation_id"])
        self.assertEqual(approval.amount + retry.amount, Decimal("10.00"))

    def test_only_a_failed_clip_is_retried(self):
        _project, (clip, _failed) = self.finished()

        response, delay = self.post(clip_url(clip, "retry"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "not_failed"))
        self.assertEqual(response.data["error"], "El clip no está en error.")
        delay.assert_not_called()

    def test_a_retry_while_the_project_renders_is_refused(self):
        _project, (_clip, failed, third) = self.finished(statuses=(DONE, FAILED, DONE))
        self.post(clip_url(third, "render"))

        response, delay = self.post(clip_url(failed, "retry"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "project_rendering"))
        delay.assert_not_called()
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)
        failed.refresh_from_db()
        self.assertEqual(failed.status, FAILED)


class RerenderTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    def test_a_whole_video_renders_again_with_the_new_style_and_pays_its_render(self):
        project, clip = self.whole_video()

        response, delay = self.post(project_url(project, "rerender"), {
            "editing_style": "hormozi", "add_subtitles": True, "subtitle_size": "large", "render_resolution": "4K",
        })

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(response.data["final_cost"], Decimal("15.00"))  # its 30 s at 720p
        delay.assert_called_once_with(str(clip.pk))
        project.refresh_from_db()
        self.assertEqual((project.editing_style, project.add_subtitles, project.subtitle_size), ("hormozi", True, "large"))
        self.assertNotIn("render_resolution", project.metadata)  # priced at creation: never changes
        self.assertTrue(_whole_video_render_allowed(project.pk, clip_id=clip.pk))

        self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.clip_render(project).status, CHARGED)
        self.creation.refresh_from_db()
        self.assertEqual((self.creation.status, self.creation.amount), (CHARGED, Decimal("15.00")))  # untouched
        self.assertEqual(self.balances(), (Decimal("85.00"), Decimal("0")))

    def test_a_failed_whole_video_is_rendered_again_paid_and_refunded_if_it_fails_again(self):
        project, clip = self.whole_video(clip_status=FAILED, stage=PipelineStage.PARTIAL, creation=REFUNDED)

        response, _delay = self.post(project_url(project, "rerender"))
        self.render_with(clip, side_effect=RuntimeError("bad encode"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(self.clip_render(project).status, REFUNDED)
        self.creation.refresh_from_db()
        self.assertEqual(self.creation.status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_every_clip_of_a_clips_project_and_only_the_delivered_share_is_charged(self):
        project, clips = self.finished(statuses=(DONE, FAILED, DONE))
        suggested = VideoClip.objects.create(project=project, title="Sugerido", start_time=90, end_time=95, status=DRAFT)

        response, delay = self.post(project_url(project, "rerender"), {"render_layout": "blurred"})

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(response.data["final_cost"], Decimal("15.00"))  # 30 s; never the suggested draft
        self.assertEqual(sorted(call.args[0] for call in delay.call_args_list), sorted(str(clip.pk) for clip in clips))
        self.render_with(clips[0])
        self.render_with(clips[1], side_effect=RuntimeError("bad encode"))
        self.render_with(clips[2])

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.PARTIAL)
        txn = self.clip_render(project)
        self.assertEqual((txn.status, txn.amount), (CHARGED, Decimal("10.00")))  # 20 of 30 s
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))
        suggested.refresh_from_db()
        self.assertEqual(suggested.status, DRAFT)

    def test_while_an_approval_or_an_auto_render_is_open_it_is_refused(self):
        project, clips = self.finished()
        # Their render is in flight: a clip still renders, the reservation is open.
        open_render = reserve_funds(self.wallet.id, Decimal("10.00"))
        VideoClip.objects.filter(pk=clips[0].pk).update(status=RENDERING, render_claim_token=uuid.uuid4())
        approval = {key: value for key, value in project.metadata.items() if not key.startswith("render_")}
        for metadata in (
            {**project.metadata, "render_reservation_id": str(open_render.id)},
            {**approval, "auto_render_reservation_id": str(open_render.id)},
        ):
            with self.subTest(metadata=sorted(metadata)):
                VideoProject.objects.filter(pk=project.pk).update(
                    status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
                    metadata=metadata,
                )
                response, delay = self.post(project_url(project, "rerender"))
                self.assertEqual(response.status_code, 409, response.content[:300])
                self.assertIn(response.data["code"], ("clip_rendering", "project_rendering"))
                delay.assert_not_called()
                self.assertEqual(Transaction.objects.get(pk=open_render.pk).status, RESERVED)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_an_approval_in_flight_through_its_endpoint_blocks_a_rerender(self):
        project, (clip,) = self.finished(statuses=(DONE,))
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED, metadata={},
        )
        VideoClip.objects.filter(pk=clip.pk).update(status=DRAFT, s3_object_key=None)
        approved, _delay = self.post(reverse("project-approve-paper-edit", args=[project.pk]), {
            "approved_segments": [{"start": 0, "end": 10, "clip_id": str(clip.pk)}],
        })
        self.assertEqual(approved.status_code, 200, approved.content[:300])

        rerender, delay = self.post(project_url(project, "rerender"))
        render, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual((rerender.status_code, render.status_code), (409, 409))
        delay.assert_not_called()
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_an_invalid_style_or_another_workspaces_brand_kit_is_a_400(self):
        project, _clip = self.whole_video()
        stranger = User.objects.create_user(username="ajeno", email="ajeno@test.com", password="pw")
        foreign_kit = BrandKit.objects.create(
            workspace=Workspace.objects.create(name="Ajeno", owner=stranger), user=stranger, name="Ajeno",
        )

        for body in ({"render_layout": "nope"}, {"brand_kit": str(foreign_kit.pk)}):
            with self.subTest(body=body):
                response, delay = self.post(project_url(project, "rerender"), body)
                self.assertEqual(response.status_code, 400, response.content[:300])
                delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())

    def test_a_project_without_clips_has_nothing_to_render(self):
        project, _clip = self.whole_video(clip_status=None, stage=PipelineStage.FAILED, creation=REFUNDED)

        response, _delay = self.post(project_url(project, "rerender"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "no_clips"))


# --- Lost messages and the sweeps ----------------------------------------------

class LostRenderTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    def lose_the_messages(self, url):
        with patch(CLIP_DELAY, side_effect=OperationalError("broker down")), \
                patch(RESUME, side_effect=OperationalError("broker down")), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, 202, response.content[:300])

    def age(self, project):
        past = timezone.now() - timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 60)
        project.refresh_from_db()
        Transaction.objects.filter(pk=project.metadata[CLIP_RENDER.reservation_key]).update(created_at=past)

    def test_the_recovery_queues_a_lost_clip_again_and_it_is_charged(self):
        project, (clip, _failed) = self.finished()
        self.lose_the_messages(clip_url(clip, "render"))
        self.age(project)

        with patch(CLIP_DELAY) as delay:
            rows = recover_stuck_renders_now(apply=True)

        row = next(row for row in rows if row["project"] == project.pk)
        self.assertEqual((row["kind"], row["action"], row["result"]), ("clips", "requeue", "requeued"))
        delay.assert_called_once_with(str(clip.pk))
        self.render_with(clip)
        self.assertEqual(self.clip_render(project).status, CHARGED)

    def test_the_recovery_also_brings_back_a_lost_whole_video_rerender(self):
        project, clip = self.whole_video()
        self.lose_the_messages(project_url(project, "rerender"))
        self.age(project)

        with patch(CLIP_DELAY) as delay:
            rows = recover_stuck_renders_now(apply=True)

        row = next(row for row in rows if row["project"] == project.pk)
        self.assertEqual((row["action"], row["reason"]), ("requeue", "clips_left"))
        delay.assert_called_once_with(str(clip.pk))

    def test_a_resume_of_the_pipeline_queues_it_and_never_reserves_more(self):
        project, clip = self.whole_video()
        self.lose_the_messages(project_url(project, "rerender"))

        with patch(CLIP_DELAY) as delay:
            _run_ingestion(project.pk, attempt=1)

        delay.assert_called_once_with(str(clip.pk))
        project.refresh_from_db()
        self.assertEqual((project.pipeline_stage, project.pipeline_error_code), (PipelineStage.RENDER_DISPATCHED, ""))
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_an_auto_render_project_never_reserves_its_auto_render_again(self):
        project, (clip, _failed) = self.finished()
        settled = reserve_funds(self.wallet.id, Decimal("1.00"))
        rollback_reservation(settled.id)
        metadata = {key: value for key, value in project.metadata.items() if not key.startswith("render_")}
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **metadata, "auto_render_reservation_id": str(settled.id), "auto_render_clip_ids": [str(clip.pk)],
        })
        self.lose_the_messages(clip_url(clip, "render"))

        with patch(CLIP_DELAY) as delay:
            self.assertTrue(_reserve_auto_render(project.pk, resuming=True))
            _run_ingestion(project.pk, attempt=1)

        delay.assert_called_once_with(str(clip.pk))
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_the_stale_sweep_settles_it_by_what_was_delivered(self):
        project, clips = self.finished(statuses=(DONE, FAILED, DONE))
        self.post(project_url(project, "rerender"))
        # Delivered one, failed one, the third clip's worker never came back;
        # then the project was closed by hand without settling.
        VideoClip.objects.filter(pk=clips[0].pk).update(status=DONE)
        VideoClip.objects.filter(pk__in=[clips[1].pk, clips[2].pk]).update(status=FAILED)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.PARTIAL, status=VideoProject.Status.PARTIAL)
        txn = self.clip_render(project)
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.now() - timedelta(days=2))

        out = StringIO()
        call_command("settle_stale_reservations", "--apply", stdout=out)

        line = next(item for item in out.getvalue().splitlines() if f"transaction={txn.pk} " in item)
        self.assertIn("kind=clip_render", line)
        self.assertIn("action=commit reason=clips_delivered result=committed", line)
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.amount), (CHARGED, Decimal("5.00")))  # 10 of 30 s


# --- projects/:id/redispatch/ --------------------------------------------------

class RedispatchTests(ClipActionsHarness, APITestCase):
    def failed(self, stage_status, **fields):
        """A clips project that failed after ``stage_status`` stages, its creation refunded."""
        creation = reserve_funds(self.wallet.id, Decimal("12.50"))
        rollback_reservation(creation.id)
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Falló", source_file="videos/raw/f.mp4",
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
            pipeline_stage_status=stage_status, pipeline_error_code=fields.pop("code", "CapacityTimeoutError"),
            metadata={"reservation_id": str(creation.id), **fields.pop("metadata", {})}, **fields,
        )
        self.creation = creation
        return project

    def redispatch(self, project, fail=None):
        with patch(INGESTION_DELAY, side_effect=fail) as delay:
            response = self.client.post(project_url(project, "redispatch"))
        return response, delay

    def test_work_that_was_refunded_is_reserved_again_with_the_same_amount(self):
        project = self.failed({"audio_extracted": "completed", "transcribed": "failed"})

        response, delay = self.redispatch(project)

        self.assertEqual(response.status_code, 202, response.content[:300])
        delay.assert_called_once_with(str(project.pk))
        project.refresh_from_db()
        new = Transaction.objects.get(pk=project.metadata["reservation_id"])
        self.assertEqual((new.status, new.amount), (RESERVED, Decimal("12.50")))
        self.assertEqual(project.metadata["replaced_reservation_ids"], [str(self.creation.pk)])
        self.assertEqual(response.data["cost_details"]["transaction_id"], new.pk)
        self.assertEqual(response.data["id"], str(project.pk))
        self.assertEqual(self.balances(), (Decimal("87.50"), Decimal("12.50")))
        # The task reopens it: the view never moves its stage.
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)

    def test_a_second_request_reuses_the_reservation(self):
        project = self.failed({"audio_extracted": "completed"})

        first, _delay = self.redispatch(project)
        second, delay = self.redispatch(project)

        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertNotIn("cost_details", second.data)
        delay.assert_called_once()
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_without_balance_nothing_is_queued(self):
        project = self.failed({"audio_extracted": "completed"})
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("12.49"))

        response, delay = self.redispatch(project)

        self.assertEqual(response.status_code, 402, response.content[:300])
        self.assertEqual(response.data["required"], Decimal("12.5"))
        delay.assert_not_called()
        project.refresh_from_db()
        self.assertEqual(project.metadata["reservation_id"], str(self.creation.pk))

    def test_a_broker_that_refuses_it_gives_the_reservation_back(self):
        project = self.failed({"audio_extracted": "completed"})

        response, _delay = self.redispatch(project, fail=OperationalError("broker down"))

        self.assertEqual(response.status_code, 503, response.content[:300])
        project.refresh_from_db()
        self.assertEqual(Transaction.objects.get(pk=project.metadata["reservation_id"]).status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_clips_already_selected_resume_free(self):
        project = self.failed({"audio_extracted": "completed", "transcribed": "completed", "moderated": "completed",
                               "clips_selected": "completed"})
        VideoClip.objects.create(project=project, title="Sugerido", start_time=0, end_time=5)
        Transaction.objects.filter(pk=self.creation.pk).update(status=CHARGED)

        response, delay = self.redispatch(project)

        self.assertEqual(response.status_code, 202, response.content[:300])
        delay.assert_called_once()
        self.assertNotIn("cost_details", response.data)
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())

    def test_a_refunded_whole_video_reserves_its_render_again(self):
        project, _clip = self.whole_video(clip_status=DRAFT, stage=PipelineStage.FAILED, creation=REFUNDED)

        response, delay = self.redispatch(project)

        self.assertEqual(response.status_code, 202, response.content[:300])
        project.refresh_from_db()
        self.assertEqual(Transaction.objects.get(pk=project.metadata["reservation_id"]).amount, Decimal("15.00"))
        delay.assert_called_once()

    def test_what_cannot_resume_is_a_409(self):
        cases = []
        cases.append(("not_failed", self.finished()[0]))
        cases.append(("moderation_blocked", self.failed({"audio_extracted": "completed"}, code="ModerationBlockedError")))
        settled = reserve_funds(self.wallet.id, Decimal("5.00"))
        rollback_reservation(settled.id)
        approved = self.failed(
            {name: "completed" for name in ("audio_extracted", "transcribed", "moderated", "clips_selected", "render_dispatched")},
            metadata={"render_reservation_id": str(settled.id), "render_clip_ids": []},
        )
        VideoClip.objects.create(project=approved, title="Aprobado", start_time=0, end_time=5)
        cases.append(("render_settled", approved))
        cases.append(("use_rerender", self.whole_video(clip_status=FAILED, stage=PipelineStage.FAILED, creation=REFUNDED)[0]))
        for code, project in cases:
            with self.subTest(code=code):
                response, delay = self.redispatch(project)
                self.assertEqual((response.status_code, response.data["code"]), (409, code))
                delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).exists())


# --- Two requests at the same time ---------------------------------------------

class ConcurrentPaidRenderTests(ClipActionsHarness, APITransactionTestCase):
    """Real commits and two threads: the project row lock lets one request through."""

    def race(self, url):
        barrier = threading.Barrier(2)
        responses = []

        def request():
            client = APIClient()
            client.force_authenticate(user=self.user)
            client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
            try:
                barrier.wait(10)
                responses.append(client.post(url, {}, format="json"))
            finally:
                connection.close()

        with patch(CLIP_DELAY) as delay, patch(INGESTION_DELAY) as ingestion:
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(len(responses), 2)
        return sorted(response.status_code for response in responses), responses, delay, ingestion

    def test_a_double_click_on_render_reserves_and_queues_once(self):
        project, (clip, _failed) = self.finished()

        statuses, responses, delay, _ingestion = self.race(clip_url(clip, "render"))

        self.assertEqual(statuses, [202, 409])
        refused = next(response for response in responses if response.status_code == 409)
        self.assertIn(refused.data["code"], ("clip_rendering", "project_rendering"))
        delay.assert_called_once_with(str(clip.pk))
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("5.00")))

    def test_a_double_click_on_retry_and_rerender_together_reserves_once(self):
        project, (_clip, failed) = self.finished()

        barrier = threading.Barrier(2)
        responses = []

        def request(url):
            client = APIClient()
            client.force_authenticate(user=self.user)
            client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
            try:
                barrier.wait(10)
                responses.append(client.post(url, {}, format="json"))
            finally:
                connection.close()

        with patch(CLIP_DELAY):
            threads = [
                threading.Thread(target=request, args=(clip_url(failed, "retry"),)),
                threading.Thread(target=request, args=(project_url(project, "rerender"),)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)

        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)

    def test_a_double_click_on_redispatch_reserves_once(self):
        creation = reserve_funds(self.wallet.id, Decimal("12.50"))
        rollback_reservation(creation.id)
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Falló", source_file="videos/raw/f.mp4",
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
            pipeline_stage_status={"audio_extracted": "completed"}, pipeline_error_code="CapacityTimeoutError",
            metadata={"reservation_id": str(creation.id)},
        )

        statuses, _responses, _delay, ingestion = self.race(project_url(project, "redispatch"))

        self.assertEqual(statuses, [202, 202])  # the second one reuses the first's reservation
        self.assertEqual(ingestion.call_count, 2)  # two messages, one project lock: harmless
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=RESERVED).count(), 1)
        self.assertEqual(self.balances(), (Decimal("87.50"), Decimal("12.50")))
