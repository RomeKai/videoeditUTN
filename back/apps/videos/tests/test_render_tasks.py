from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _claim_clip_for_render, render_clip_task, upload_to_social_network
from apps.videos.tasks.render import RENDER_CLIP_MAX_RETRIES, _is_retryable_render_error
from apps.videos.tests.fakes import CapturedTaskFailures
from backend.celery import app as celery_app

User = get_user_model()

RENDER = "apps.videos.services.render_engine.RenderEngine.render_clip"
DELETE = "apps.videos.services.storage_service.CloudflareR2Manager.delete_object"


class RenderHarness(CapturedTaskFailures):
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

    def test_duplicate_delivery_while_rendering_does_not_render_twice(self):
        clip = self.make_clip()
        nested_results = []

        def render_with_duplicate_delivery(clip_id):
            # The same message is delivered again while the first one is rendering.
            nested_results.append(render_clip_task.apply(args=[str(clip_id)]))
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=render_with_duplicate_delivery) as render:
            render_clip_task.apply(args=[str(clip.id)])

        self.assertEqual(render.call_count, 1)
        self.assertTrue(nested_results[0].successful())
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)

    def test_clip_already_rendering_by_another_delivery_is_skipped(self):
        clip = self.make_clip(status=VideoClip.Status.RENDERING)

        with patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        render.assert_not_called()

    def test_claim_moves_a_draft_clip_to_rendering_exactly_once(self):
        clip = self.make_clip()

        self.assertTrue(_claim_clip_for_render(clip.id, redelivered=False))
        self.assertFalse(_claim_clip_for_render(clip.id, redelivered=False))

        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)

    def test_a_redelivered_message_may_reclaim_a_clip_abandoned_in_rendering(self):
        clip = self.make_clip(status=VideoClip.Status.RENDERING)

        # The worker that claimed it died: acks_late redelivers the message.
        self.assertTrue(_claim_clip_for_render(clip.id, redelivered=True))

    def test_render_task_survives_worker_loss_by_acking_late(self):
        self.assertTrue(render_clip_task.acks_late)
        self.assertTrue(render_clip_task.reject_on_worker_lost)

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
            router.route({}, "apps.videos.tasks.render_video_segments")["queue"].name, "render"
        )
        self.assertEqual(
            router.route({}, "apps.videos.tasks.process_initial_ingestion")["queue"].name, "celery"
        )


def transient_r2_error():
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "PutObject",
    )


class RenderClipRetryTests(RenderHarness, TestCase):
    """
    A clip render retries transient errors with backoff and fails at once on
    deterministic ones. The clip reaches FAILED only after its last attempt,
    and a retry never renders a clip twice nor closes the project early.
    """

    def clip_status(self, clip_id):
        return VideoClip.objects.values_list("status", flat=True).get(pk=clip_id)

    def test_a_transient_error_is_retried_and_the_clip_is_delivered(self):
        clip = self.make_clip()
        seen = []

        def flaky(clip_id):
            seen.append(self.clip_status(clip_id))
            if len(seen) == 1:
                raise transient_r2_error()
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=flaky):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        # The retry claimed the clip again like any delivery.
        self.assertEqual(seen, [VideoClip.Status.RENDERING, VideoClip.Status.RENDERING])
        clip.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)

    def test_the_clip_fails_only_after_its_last_attempt(self):
        clip = self.make_clip()
        seen = []

        def always_transient(clip_id):
            seen.append(self.clip_status(clip_id))
            raise transient_r2_error()

        with patch(RENDER, side_effect=always_transient):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        self.assertEqual(len(seen), RENDER_CLIP_MAX_RETRIES + 1)
        self.assertNotIn(VideoClip.Status.FAILED, seen)
        clip.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.status, VideoProject.Status.PARTIAL)

    def test_a_deterministic_error_fails_at_once(self):
        errors = (
            RuntimeError("moviepy exploded"),
            # MoviePy reports an unreadable source as a plain OSError.
            OSError("MoviePy error: failed to read the first frame"),
            FileNotFoundError("source.mp4"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                clip = self.make_clip()
                with patch(RENDER, side_effect=error) as render:
                    result = render_clip_task.apply(args=[str(clip.id)])

                self.assertTrue(result.failed())
                self.assertEqual(render.call_count, 1)
                self.assertEqual(self.clip_status(clip.id), VideoClip.Status.FAILED)

    def test_connection_and_database_errors_are_transient(self):
        from django.db import OperationalError

        for error in (ConnectionResetError("reset"), TimeoutError("timed out"), OperationalError("gone")):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(_is_retryable_render_error(error))

    def test_the_project_waits_for_a_clip_that_will_be_retried(self):
        done, flaky_clip = self.make_clip(), self.make_clip()
        with patch(RENDER, side_effect=self.completing_render):
            render_clip_task.apply(args=[str(done.id)])
        stages = []

        def flaky(clip_id):
            stages.append(
                VideoProject.objects.values_list("pipeline_stage", flat=True).get(pk=self.project.pk)
            )
            if len(stages) == 1:
                raise transient_r2_error()
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=flaky):
            render_clip_task.apply(args=[str(flaky_clip.id)])

        # Not closed (PARTIAL) while the second clip waited for its retry.
        self.assertEqual(stages, [PipelineStage.RENDER_DISPATCHED, PipelineStage.RENDER_DISPATCHED])
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)

    def test_a_retry_never_renders_a_clip_another_delivery_claimed(self):
        clip = self.make_clip()
        original_retry = render_clip_task.retry

        def another_delivery_claims_it(*args, **kwargs):
            # During the backoff a duplicate delivery claims the handed-back clip.
            self.assertTrue(_claim_clip_for_render(clip.id))
            return original_retry(*args, **kwargs)

        with patch(RENDER, side_effect=transient_r2_error()) as render, \
                patch.object(render_clip_task, "retry", side_effect=another_delivery_claims_it):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        self.assertEqual(render.call_count, 1)  # the retry found it claimed and skipped
        self.assertEqual(self.clip_status(clip.id), VideoClip.Status.RENDERING)

    def test_a_database_blip_while_closing_the_fan_out_does_not_render_again(self):
        from django.db import OperationalError

        from apps.videos.tasks.render import finalize_project_render as real_finalize

        clip = self.make_clip()
        calls = []

        def finalize_once_failing(project_id):
            calls.append(project_id)
            if len(calls) == 1:
                raise OperationalError("connection lost")
            return real_finalize(project_id)

        with patch(RENDER, side_effect=self.completing_render) as render, \
                patch("apps.videos.tasks.render.finalize_project_render", side_effect=finalize_once_failing):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        self.assertEqual(render.call_count, 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)

    def test_the_engine_leaves_a_failed_clip_to_the_task_that_claimed_it(self):
        from apps.videos.services.render_engine import RenderEngine

        clip = self.make_clip(status=VideoClip.Status.RENDERING)  # claimed by the task

        with self.assertRaises(ValueError):  # the project has no source file
            RenderEngine.render_clip(clip.id)

        # Not DRAFT: a duplicate delivery cannot take it before the task decides.
        self.assertEqual(self.clip_status(clip.id), VideoClip.Status.RENDERING)

    def test_a_delivered_clip_is_never_marked_failed(self):
        clip = self.make_clip()

        with patch(RENDER, side_effect=self.completing_render), patch(
            "apps.videos.tasks.render.finalize_project_render", side_effect=[RuntimeError("x"), None],
        ):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        self.assertEqual(self.clip_status(clip.id), VideoClip.Status.COMPLETED)


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


class UploadToSocialNetworkTests(CapturedTaskFailures, TestCase):
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

    def test_ayrshare_errors_until_out_of_retries_fail_the_post(self):
        # Before, the last retry re-raised and the post stayed in PROCESSING forever.
        from apps.integrations.ayrshare_api import AyrshareAPIError

        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://r2/clip.mp4",
        ), patch(
            "apps.integrations.ayrshare_api.AyrshareClient.send_post",
            side_effect=AyrshareAPIError("Ayrshare error: rate limited"),
        ) as send:
            result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(send.call_count, upload_to_social_network.max_retries + 1)
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.retry_count, upload_to_social_network.max_retries + 1)
        self.assertIn("Failed after 4 attempts", self.post.error_log)

    def test_a_read_timeout_is_never_retried(self):
        # The request was sent: retrying could publish the clip twice.
        import requests

        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://r2/clip.mp4",
        ), patch(
            "apps.integrations.ayrshare_api.requests.post",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ) as post:
            result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertIn("may have been published", self.post.error_log)

    def test_a_connect_timeout_is_retried(self):
        # Nothing reached Ayrshare: safe to send again.
        import requests

        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://r2/clip.mp4",
        ), patch(
            "apps.integrations.ayrshare_api.requests.post",
            side_effect=requests.exceptions.ConnectTimeout("connect timed out"),
        ) as post:
            upload_to_social_network.apply(args=[str(self.post.id)])

        self.assertEqual(post.call_count, upload_to_social_network.max_retries + 1)

    def test_ayrshare_requests_have_a_timeout(self):
        from apps.integrations.ayrshare_api import AyrshareClient

        with patch("apps.integrations.ayrshare_api.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"id": "x"}
            AyrshareClient.send_post("key", "https://r2/clip.mp4", "caption", ["tiktok"])

        self.assertEqual(post.call_args.kwargs["timeout"], AyrshareClient.TIMEOUT_SECONDS)

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
