from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import render_clip_task, upload_to_social_network
from backend.celery import app as celery_app

User = get_user_model()

RENDER = "apps.videos.services.render_engine.RenderEngine.render_clip"
DELETE = "apps.videos.services.storage_service.CloudflareR2Manager.delete_object"


class RenderHarness:
    def setUp(self):
        super().setUp()
        user = User.objects.create_user(username="rend", email="rend@test.com", password="pw")
        workspace = Workspace.objects.create(name="Render Workspace", owner=user)
        self.project = VideoProject.objects.create(
            workspace=workspace,
            uploaded_by=user,
            title="Render",
            status=VideoProject.Status.RENDERING,
            pipeline_stage=PipelineStage.RENDER_DISPATCHED,
        )

    def make_clip(self, **kwargs):
        return VideoClip.objects.create(
            project=self.project, title="c", start_time=0, end_time=5, **kwargs
        )

    @staticmethod
    def completing_render(clip_id):
        VideoClip.objects.filter(pk=clip_id).update(
            status=VideoClip.Status.COMPLETED, s3_object_key=f"clips/{clip_id}.mp4"
        )
        return True


class RenderClipTaskTests(RenderHarness, TestCase):
    def test_failure_propagates_instead_of_returning_a_string(self):
        clip = self.make_clip()

        with patch(RENDER, side_effect=RuntimeError("moviepy exploded")):
            with self.assertRaises(RuntimeError):
                render_clip_task(str(clip.id))

    def test_failed_task_marks_clip_failed(self):
        clip = self.make_clip()

        with patch(RENDER, side_effect=RuntimeError("moviepy exploded")):
            result = render_clip_task.apply(args=[str(clip.id)])

        clip.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(clip.status, VideoClip.Status.FAILED)

    def test_rerun_on_completed_clip_is_a_noop(self):
        clip = self.make_clip(status=VideoClip.Status.COMPLETED, s3_object_key="clips/done.mp4")

        with patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        render.assert_not_called()

    def test_rerender_deletes_the_previous_r2_object(self):
        clip = self.make_clip(status=VideoClip.Status.DRAFT, s3_object_key="clips/old.mp4")

        with patch(RENDER, side_effect=self.completing_render), patch(DELETE) as delete:
            render_clip_task.apply(args=[str(clip.id)])

        delete.assert_called_once_with("clips/old.mp4")

    def test_failed_previous_delete_does_not_fail_the_render(self):
        clip = self.make_clip(status=VideoClip.Status.DRAFT, s3_object_key="clips/old.mp4")

        with patch(RENDER, side_effect=self.completing_render), patch(DELETE, side_effect=OSError("r2")):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())

    def test_all_clips_rendered_completes_the_project(self):
        clips = [self.make_clip() for _ in range(3)]

        with patch(RENDER, side_effect=self.completing_render):
            for clip in clips:
                render_clip_task.apply(args=[str(clip.id)])

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)

    def test_one_failed_clip_makes_the_project_partial_never_completed(self):
        clips = [self.make_clip() for _ in range(3)]

        def flaky_render(clip_id):
            if str(clip_id) == str(clips[1].id):
                raise RuntimeError("bad clip")
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=flaky_render):
            for clip in clips:
                render_clip_task.apply(args=[str(clip.id)])

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.PARTIAL)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    def test_project_is_not_finalized_while_clips_are_pending(self):
        first, _second = self.make_clip(), self.make_clip()

        with patch(RENDER, side_effect=self.completing_render):
            render_clip_task.apply(args=[str(first.id)])

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)

    def test_render_tasks_are_routed_to_the_render_queue(self):
        router = celery_app.amqp.router
        self.assertEqual(
            router.route({}, "apps.videos.tasks.render_clip_task")["queue"].name, "render"
        )
        self.assertEqual(
            router.route({}, "apps.videos.tasks.process_initial_ingestion")["queue"].name, "celery"
        )


class RenderNoTransactionTests(RenderHarness, TransactionTestCase):
    def test_no_transaction_is_open_while_rendering(self):
        clip = self.make_clip()
        observed = []

        def probe(clip_id):
            observed.append(connection.in_atomic_block)
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=probe):
            render_clip_task.apply(args=[str(clip.id)])

        self.assertEqual(observed, [False])


class UploadToSocialNetworkTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="soc", email="soc@test.com", password="pw")
        self.workspace = Workspace.objects.create(
            name="Social", owner=user, ayrshare_profile_key="profile-key"
        )
        project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=user, title="S")
        clip = VideoClip.objects.create(
            project=project, title="c", start_time=0, end_time=5, s3_object_key="clips/a.mp4"
        )
        self.post = ScheduledPost.objects.create(
            video_clip=clip,
            platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z",
            status=ScheduledPost.Status.QUEUED,
        )

    def test_unexpected_error_is_raised_and_post_marked_failed(self):
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            side_effect=RuntimeError("r2 down"),
        ):
            result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertIn("r2 down", self.post.error_log)

    def test_missing_post_raises_the_real_error_not_unbound_local(self):
        result = upload_to_social_network.apply(
            args=["00000000-0000-0000-0000-000000000000"]
        )

        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, ScheduledPost.DoesNotExist)

    def test_missing_profile_key_fails_the_task_and_the_post(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(ayrshare_profile_key="")

        result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
