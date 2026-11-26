from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
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
    def completing_render(clip_id, claim_token=None):
        # Like the engine: COMPLETED is final, so the claim and its start count go.
        VideoClip.objects.filter(pk=clip_id).update(
            status=VideoClip.Status.COMPLETED, s3_object_key=f"clips/{clip_id}.mp4",
            render_claim_token=None, render_unfinished_starts=0,
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

        def render_with_duplicate_delivery(clip_id, claim_token=None):
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

        self.assertTrue(_claim_clip_for_render(clip.id))
        self.assertFalse(_claim_clip_for_render(clip.id))

        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)

    def test_a_clip_that_failed_for_good_is_never_claimed_again(self):
        clip = self.make_clip(status=VideoClip.Status.FAILED)

        self.assertIsNone(_claim_clip_for_render(clip.id))
        self.assertIsNone(_claim_clip_for_render(clip.id, takeover=True))  # not even with a takeover right
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.FAILED)

    def test_a_late_message_for_a_failed_clip_renders_nothing(self):
        clip = self.make_clip(status=VideoClip.Status.FAILED)

        with patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        render.assert_not_called()
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.FAILED)

    def test_a_redelivered_message_may_reclaim_a_clip_abandoned_in_rendering(self):
        clip = self.make_clip(status=VideoClip.Status.RENDERING)

        # The worker that claimed it died: acks_late redelivers the message.
        self.assertTrue(_claim_clip_for_render(clip.id, takeover=True))

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

        def flaky_render(clip_id, claim_token=None):
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

        def flaky(clip_id, claim_token=None):
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

        def always_transient(clip_id, claim_token=None):
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

        def flaky(clip_id, claim_token=None):
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


def _upload_failed(code, status):
    """S3UploadFailedError as boto3's upload_file raises it: inside its ``except ClientError``."""
    from boto3.exceptions import S3UploadFailedError
    from botocore.exceptions import ClientError

    try:
        raise ClientError(
            {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "PutObject",
        )
    except ClientError as exc:
        try:
            raise S3UploadFailedError(f"Failed to upload clip.mp4 to bucket/key: {exc}")
        except S3UploadFailedError as wrapped:
            return wrapped


class StorageErrorClassificationTests(TestCase):
    """R2 transport errors and failed uploads are transient for renders and for the pipeline."""

    def transport_errors(self):
        import botocore.exceptions as b

        return {
            "ReadTimeoutError": b.ReadTimeoutError(endpoint_url="https://r2"),
            "ConnectTimeoutError": b.ConnectTimeoutError(endpoint_url="https://r2"),
            "EndpointConnectionError": b.EndpointConnectionError(endpoint_url="https://r2"),
            "ConnectionClosedError": b.ConnectionClosedError(endpoint_url="https://r2"),
            "SSLError": b.SSLError(endpoint_url="https://r2", error="eof"),
            "ProxyConnectionError": b.ProxyConnectionError(proxy_url="https://proxy"),
        }

    def test_botocore_transport_errors_retry_although_they_are_oserrors(self):
        for name, error in self.transport_errors().items():
            with self.subTest(error=name):
                self.assertTrue(_is_retryable_render_error(error))

    def test_a_failed_upload_retries_unless_r2_rejected_it(self):
        from boto3.exceptions import S3UploadFailedError

        from apps.videos.tasks import _is_retryable_pipeline_error

        cases = {
            "SlowDown 503": (_upload_failed("SlowDown", 503), True),
            "InternalError 500": (_upload_failed("InternalError", 500), True),
            "no detail": (S3UploadFailedError("Failed to upload x to b/k"), True),
            "AccessDenied 403": (_upload_failed("AccessDenied", 403), False),
            "NoSuchBucket 404": (_upload_failed("NoSuchBucket", 404), False),
        }
        for name, (error, retryable) in cases.items():
            with self.subTest(error=name):
                # The ingestion uploads to R2 too: same answer for both.
                self.assertEqual(_is_retryable_render_error(error), retryable)
                self.assertEqual(_is_retryable_pipeline_error(error), retryable)


class RenderClipUploadRetryTests(RenderHarness, TestCase):
    """The real engine runs (MoviePy mocked) and the R2 client's upload_file fails."""

    ENGINE = "apps.videos.services.render_engine"

    def setUp(self):
        super().setUp()
        import tempfile

        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = media.name
        VideoProject.objects.filter(pk=self.project.pk).update(source_file="videos/raw/source.mp4")

    def render(self, clip, *upload_outcomes):
        from unittest.mock import MagicMock

        from django.test import override_settings

        client = MagicMock()
        client.upload_file.side_effect = list(upload_outcomes)
        layout = MagicMock()
        layout.apply.return_value = MagicMock()
        with override_settings(MEDIA_ROOT=self.media, USE_R2=True, CLOUDFLARE_R2_BUCKET_NAME="bucket"), \
                patch(f"{self.ENGINE}.VideoFileClip"), \
                patch(f"{self.ENGINE}.get_layout_strategy", return_value=layout), \
                patch("apps.videos.services.storage_service._get_r2_client", return_value=client):
            result = render_clip_task.apply(args=[str(clip.id)])
        return result, client

    def test_a_throttled_upload_is_retried_and_the_clip_delivered(self):
        clip = self.make_clip()

        result, client = self.render(clip, _upload_failed("SlowDown", 503), None)

        self.assertTrue(result.successful())
        self.assertEqual(client.upload_file.call_count, 2)
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)
        self.assertTrue(clip.s3_object_key.startswith("videos/"))

    def test_a_read_timeout_on_the_upload_is_retried(self):
        import botocore.exceptions as b

        clip = self.make_clip()

        result, client = self.render(clip, b.ReadTimeoutError(endpoint_url="https://r2"), None)

        self.assertTrue(result.successful())
        self.assertEqual(client.upload_file.call_count, 2)
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.COMPLETED)

    def test_an_upload_r2_rejects_fails_the_clip_at_once(self):
        clip = self.make_clip()

        result, client = self.render(clip, _upload_failed("AccessDenied", 403))

        self.assertTrue(result.failed())
        self.assertEqual(client.upload_file.call_count, 1)
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.FAILED)


class RenderClipTimeLimitTests(RenderHarness, TestCase):
    """
    A clip render that hangs or runs too long fails and frees its claim, well
    before recover_stuck_renders would take a RENDERING clip over; and no
    render runs without its start recorded, which that recovery reads.
    """

    def test_the_clip_render_has_soft_and_hard_limits_below_the_stuck_threshold(self):
        soft, hard = render_clip_task.soft_time_limit, render_clip_task.time_limit
        self.assertEqual(soft, settings.RENDER_CLIP_SOFT_TIME_LIMIT)
        self.assertEqual(hard, settings.RENDER_CLIP_TIME_LIMIT)
        self.assertLess(soft, hard)
        self.assertLess(hard, settings.RENDER_STUCK_AFTER_SECONDS)
        self.assertLess(hard, settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"])

    def test_a_render_past_its_soft_limit_fails_for_good_and_frees_its_claim(self):
        clip = self.make_clip()

        with patch(RENDER, side_effect=SoftTimeLimitExceeded()) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        render.assert_called_once()  # never retried
        row = VideoClip.objects.get(pk=clip.pk)
        self.assertEqual(
            (row.status, row.render_claim_token, row.render_unfinished_starts), (VideoClip.Status.FAILED, None, 0),
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)
        self.assertFalse(_is_retryable_render_error(SoftTimeLimitExceeded()))

    def test_a_render_never_runs_without_its_start_recorded(self):
        from apps.videos.tasks import render as render_module

        clip = self.make_clip()
        real_merge = render_module._merge_metadata
        calls = []
        recorded = []

        def database_blip_once(project_id, updates, **kwargs):
            calls.append(updates)
            if len(calls) == 1:
                raise OperationalError("server closed the connection")
            return real_merge(project_id, updates, **kwargs)

        def render(clip_id, claim_token=None):
            metadata = VideoProject.objects.values_list("metadata", flat=True).get(pk=self.project.pk) or {}
            recorded.append("render_clip_started_at" in metadata)
            return self.completing_render(clip_id)

        with patch.object(render_module, "_merge_metadata", side_effect=database_blip_once), \
                patch(RENDER, side_effect=render):
            result = render_clip_task.apply(args=[str(clip.id)])

        # The first delivery handed its claim back and its retry rendered, with the start recorded.
        self.assertTrue(result.successful())
        self.assertEqual(len(calls), 2)
        self.assertEqual(recorded, [True])
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.COMPLETED)


class RenderClipRetryNotQueuedTests(RenderHarness, TestCase):
    def test_a_retry_that_cannot_be_queued_fails_the_clip_and_closes_the_project(self):
        from celery.exceptions import Reject

        clip = self.make_clip()

        def broker_down(*args, **kwargs):
            # What Task.retry raises when publishing the retry fails.
            raise Reject(RuntimeError("broker down"), requeue=False)

        with patch(RENDER, side_effect=transient_r2_error()), \
                patch.object(render_clip_task, "retry", side_effect=broker_down):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        clip.refresh_from_db()
        self.project.refresh_from_db()
        # Not left DRAFT with no message to render it: on_failure ran.
        self.assertEqual(clip.status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)


class RenderNoTransactionTests(RenderHarness, TransactionTestCase):
    def test_no_transaction_is_open_while_rendering(self):
        clip = self.make_clip()
        observed = []

        def probe(clip_id, claim_token=None):
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

    def test_a_post_deleted_before_its_task_runs_publishes_nothing(self):
        """Deleted with its clip, project or workspace after it was queued: no failure, no call."""
        post_id = str(self.post.id)
        self.post.delete()

        with patch("apps.integrations.ayrshare_api.AyrshareClient.send_post") as send, \
                self.assertLogs("apps.videos.tasks.social", level="WARNING") as logs:
            result = upload_to_social_network.apply(args=[post_id])

        self.assertTrue(result.successful())
        self.assertEqual(result.result, "Post Deleted")
        send.assert_not_called()
        self.assertIn(f"publish.post_deleted post_id={post_id} when=before_publishing", logs.output[0])

    def test_a_post_deleted_while_it_was_published_is_not_inserted_again(self):
        """Its row went while Ayrshare answered: the task ends, and no row comes back."""
        post_id = str(self.post.id)

        def publish_and_delete(**kwargs):
            ScheduledPost.objects.filter(id=post_id).delete()
            return {"id": "ayr-1"}

        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://r2/clip.mp4",
        ), patch(
            "apps.integrations.ayrshare_api.AyrshareClient.send_post", side_effect=publish_and_delete,
        ), self.assertLogs("apps.videos.tasks.social", level="WARNING") as logs:
            result = upload_to_social_network.apply(args=[post_id])

        self.assertTrue(result.successful(), result.result)
        self.assertFalse(ScheduledPost.objects.filter(id=post_id).exists())
        self.assertTrue(any("when=after_publishing" in line for line in logs.output))

    def test_publishing_only_writes_its_own_columns(self):
        """A caption edited while the post was being sent is not overwritten by the task's copy."""
        def publish(**kwargs):
            ScheduledPost.objects.filter(id=self.post.id).update(retry_count=7)
            return {"id": "ayr-1"}

        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://r2/clip.mp4",
        ), patch("apps.integrations.ayrshare_api.AyrshareClient.send_post", side_effect=publish):
            upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.PUBLISHED)
        self.assertEqual(self.post.retry_count, 7)

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

    def test_a_lock_that_cannot_be_taken_is_retried_then_fails_the_post(self):
        """Redis down for the lock: retried with backoff, never published, and out of retries FAILED, not QUEUED."""
        from unittest.mock import MagicMock

        from redis.exceptions import ConnectionError as RedisConnectionError

        locks = MagicMock()
        locks.add.side_effect = RedisConnectionError("down")
        with patch("apps.videos.tasks.social.caches", {"locks": locks}), \
                patch.object(upload_to_social_network, "retry", wraps=upload_to_social_network.retry) as retry, \
                patch("apps.integrations.ayrshare_api.AyrshareClient.send_post") as send:
            result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        send.assert_not_called()
        self.assertEqual(locks.add.call_count, upload_to_social_network.max_retries + 1)
        self.assertEqual([call.kwargs["countdown"] for call in retry.call_args_list], [60, 120, 240])
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "Lock unavailable after 4 attempts: ConnectionError")
        locks.delete.assert_not_called()

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

    def test_missing_post_ends_the_task_without_an_unbound_local(self):
        # A post that does not exist (deleted, or never there) is nothing to publish: no failure, no retry.
        result = upload_to_social_network.apply(
            args=["00000000-0000-0000-0000-000000000000"]
        )

        self.assertTrue(result.successful())
        self.assertEqual(result.result, "Post Deleted")

    def test_missing_profile_key_fails_the_task_and_the_post(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(ayrshare_profile_key="")

        result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.post.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
