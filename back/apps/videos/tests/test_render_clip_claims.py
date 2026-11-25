"""
Two copies of the same render_clip_task message can run at once: with Redis, a
broker connection blip redelivers a running acks_late task while its worker
keeps rendering. The redelivered copy takes the clip over with a new owner
token, and every later write of a delivery (hand back before a retry, FAILED,
COMPLETED) only applies while it still owns the clip. Render starts whose
delivery never ended are capped (RENDER_CLIP_MAX_STARTS).
"""

import tempfile
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock, patch

from celery.exceptions import Reject
from celery.signals import task_prerun
from django.db import OperationalError, connection
from django.test import TestCase, override_settings

from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _claim_clip_for_render, finalize_project_render, render_clip_task
from apps.videos.tasks import render as render_module
from apps.videos.tasks.render import RENDER_CLIP_MAX_RETRIES, RENDER_TAKEOVER_HEADER, TAKEOVER_OBSERVE
from apps.videos.tests.test_render_tasks import RenderHarness, transient_r2_error

RENDER = "apps.videos.services.render_engine.RenderEngine.render_clip"
ALLOWED = "apps.videos.tasks.ingestion._whole_video_render_allowed"
ENGINE = "apps.videos.services.render_engine"


class WorkerKilled(BaseException):
    """A worker killed mid-render (an OOM): nothing of the task runs after it, not even on_failure."""


def take_over(clip_id):
    """The redelivered copy B claims the clip while A still renders it. Returns B's token."""
    _claim_clip_for_render(clip_id, takeover=True)
    return VideoClip.objects.values_list("render_claim_token", flat=True).get(pk=clip_id)


def redelivered_copy(clip_id):
    """
    Runs a copy of the message that the broker delivered again: its first
    attempt carries ``delivery_info['redelivered']`` (a retry is a new message).
    """
    def mark_redelivered(sender=None, task=None, **kwargs):
        if task.name == render_clip_task.name and not task.request.retries:
            task.request.delivery_info = {**(task.request.delivery_info or {}), "redelivered": True}

    task_prerun.connect(mark_redelivered, weak=False)
    try:
        return render_clip_task.apply(args=[str(clip_id)])
    finally:
        task_prerun.disconnect(mark_redelivered)


class ClaimHarness(RenderHarness):
    """The real RenderEngine, with MoviePy, the layout and the R2 client mocked."""

    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = media.name
        VideoProject.objects.filter(pk=self.project.pk).update(source_file="videos/raw/source.mp4")
        self.r2 = MagicMock()
        self.layout = MagicMock()
        self.layout.apply.return_value = MagicMock()

    @contextmanager
    def engine(self):
        with override_settings(MEDIA_ROOT=self.media, USE_R2=True, CLOUDFLARE_R2_BUCKET_NAME="bucket"), \
                patch(f"{ENGINE}.VideoFileClip"), \
                patch(f"{ENGINE}.get_layout_strategy", return_value=self.layout), \
                patch("apps.videos.services.storage_service._get_r2_client", return_value=self.r2):
            yield self.r2

    def dead_owner_clip(self):
        """A claimed the clip and its worker died: RENDERING under A's token, forever."""
        clip = self.make_clip()
        with patch(RENDER, side_effect=WorkerKilled), self.assertRaises(WorkerKilled):
            render_clip_task.apply(args=[str(clip.id)])
        return clip

    def retry_headers(self):
        """Patches render_clip_task.retry to record the headers each retry is queued with."""
        original_retry = render_clip_task.retry
        sent = []

        def recording_retry(*args, **kwargs):
            sent.append(kwargs.get("headers"))
            return original_retry(*args, **kwargs)

        return sent, patch.object(render_clip_task, "retry", side_effect=recording_retry)

    def uploaded_keys(self):
        return [call.args[2] for call in self.r2.upload_file.call_args_list]

    def clip_row(self, clip_id):
        return VideoClip.objects.get(pk=clip_id)


class ClipOwnershipTests(ClaimHarness, TestCase):
    def test_a_takeover_replaces_the_owner_token(self):
        clip = self.make_clip()

        first = _claim_clip_for_render(clip.id)
        second = _claim_clip_for_render(clip.id, takeover=True)

        self.assertNotEqual(first.token, second.token)
        clip.refresh_from_db()
        self.assertEqual(clip.render_claim_token, second.token)
        self.assertEqual(clip.render_unfinished_starts, 2)

    def test_a_live_claim_survives_the_transient_failure_of_the_copy_that_lost_it(self):
        clip = self.make_clip()
        tokens = {}

        def a_fails_after_b_took_over(clip_id, claim_token=None):
            tokens["b"] = take_over(clip_id)
            raise transient_r2_error()

        with patch(RENDER, side_effect=a_fails_after_b_took_over) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        # A neither handed B's claim back (DRAFT) nor retried: no third render.
        self.assertEqual(render.call_count, 1)
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)
        self.assertEqual(clip.render_claim_token, tokens["b"])
        self.assertEqual(clip.render_unfinished_starts, 1)  # only B's start is still running

    def test_the_final_failure_of_the_copy_that_lost_the_clip_leaves_it_to_the_owner(self):
        clip = self.make_clip()

        def a_fails_for_good(clip_id, claim_token=None):
            take_over(clip_id)
            raise RuntimeError("moviepy exploded")

        with patch(RENDER, side_effect=a_fails_for_good):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        clip.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)

    def test_a_delivery_that_fails_before_claiming_never_fails_a_clip_another_one_renders(self):
        clip = self.make_clip()
        owner = _claim_clip_for_render(clip.id)  # a live delivery is rendering it

        with patch(ALLOWED, side_effect=RuntimeError("boom")), patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        render.assert_not_called()
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)
        self.assertEqual(clip.render_claim_token, owner.token)

    def test_a_last_attempt_that_fails_before_the_claim_fails_a_clip_nobody_renders(self):
        clip = self.make_clip()

        # A database blip before the claim, on every attempt.
        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)  # not open forever

    def test_a_retry_not_queued_before_the_claim_fails_a_clip_nobody_renders(self):
        clip = self.make_clip()

        def broker_down(*args, **kwargs):
            raise Reject(RuntimeError("broker down"), requeue=False)

        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render, \
                patch.object(render_clip_task, "retry", side_effect=broker_down):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    def test_a_redelivered_copy_whose_retry_is_not_queued_before_the_claim_fails_a_dead_owners_clip(self):
        clip = self.dead_owner_clip()

        def broker_down(*args, **kwargs):
            raise Reject(RuntimeError("broker down"), requeue=False)

        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render, \
                patch.object(render_clip_task, "retry", side_effect=broker_down):
            result = redelivered_copy(clip.id)

        self.assertTrue(result.failed())
        render.assert_not_called()
        row = self.clip_row(clip.id)
        self.project.refresh_from_db()
        self.assertEqual(row.status, VideoClip.Status.FAILED)
        self.assertIsNone(row.render_claim_token)  # the dead owner can no longer save it
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    def test_a_redelivered_copy_that_always_fails_before_the_claim_fails_a_dead_owners_clip(self):
        clip = self.dead_owner_clip()

        # Every attempt fails before the claim: the retries carry the takeover right.
        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render:
            result = redelivered_copy(clip.id)

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    def test_the_retry_of_a_redelivered_copy_that_failed_before_the_claim_takes_over(self):
        clip = self.dead_owner_clip()
        dead_owner = self.clip_row(clip.id).render_claim_token
        answers = []

        def blip_then_allowed(project_id, clip_id=None):
            answers.append(project_id)
            if len(answers) == 1:
                raise OperationalError("db blip")  # the redelivered copy's first attempt
            return True

        sent, recording_retry = self.retry_headers()
        with patch(ALLOWED, side_effect=blip_then_allowed), recording_retry, \
                patch(RENDER, side_effect=self.completing_render) as render:
            result = redelivered_copy(clip.id)

        self.assertTrue(result.successful())
        self.assertEqual(sent, [{RENDER_TAKEOVER_HEADER: str(dead_owner)}])  # pinned to the dead owner
        self.assertEqual(render.call_count, 1)  # the retry took the clip over and rendered it
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)

    def test_the_takeover_right_never_reaches_an_owner_that_claimed_after_the_blip(self):
        clip = self.make_clip()
        owner_a = _claim_clip_for_render(clip.id).token  # A is alive: a broker blip redelivers B
        original_retry = render_clip_task.retry
        state = {}

        def a_hands_back_and_its_retry_claims(*args, **kwargs):
            if "a2" not in state:
                render_module._release_clip_claim(clip.id, owner_a)  # A: transient error
                state["a2"] = _claim_clip_for_render(clip.id).token  # A's retry claims and renders
                state["headers"] = kwargs.get("headers")
            return original_retry(*args, **kwargs)

        # B fails before its claim on every attempt.
        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render, \
                patch.object(render_clip_task, "retry", side_effect=a_hands_back_and_its_retry_claims):
            result = redelivered_copy(clip.id)

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.assertEqual(state["headers"], {RENDER_TAKEOVER_HEADER: str(owner_a)})
        row = self.clip_row(clip.id)
        self.project.refresh_from_db()
        self.assertEqual(row.status, VideoClip.Status.RENDERING)  # A2's live claim survives
        self.assertEqual(row.render_claim_token, state["a2"])
        self.assertEqual(self.project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)

    def test_a_redelivered_copy_does_not_take_over_a_clip_claimed_after_it_came_back(self):
        clip = self.make_clip()
        owner_a = _claim_clip_for_render(clip.id).token

        owners = []

        def a_hands_back_and_its_retry_claims(project_id, clip_id=None):
            # Between B's first read of the clip (owner A) and B's claim.
            render_module._release_clip_claim(clip.id, owner_a)
            owners.append(_claim_clip_for_render(clip.id).token)
            return True

        with patch(ALLOWED, side_effect=a_hands_back_and_its_retry_claims), patch(RENDER) as render:
            result = redelivered_copy(clip.id)

        self.assertTrue(result.successful())  # "already claimed"
        render.assert_not_called()
        row = self.clip_row(clip.id)
        self.assertEqual(row.status, VideoClip.Status.RENDERING)
        self.assertEqual(row.render_claim_token, owners[0])

    def test_a_redelivered_copy_takes_over_a_clip_claimed_by_a_worker_on_the_previous_code(self):
        clip = self.make_clip(status=VideoClip.Status.RENDERING)  # claimed with no token, worker died

        with patch(RENDER, side_effect=self.completing_render) as render:
            result = redelivered_copy(clip.id)

        self.assertTrue(result.successful())
        render.assert_called_once()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)

    def test_a_redelivered_copy_that_cannot_read_the_clip_lets_its_retry_pin_the_owner(self):
        clip = self.dead_owner_clip()
        real_get = VideoClip.objects.get
        reads = []

        def first_read_fails(*args, **kwargs):
            reads.append(kwargs)
            if len(reads) == 1:
                raise OperationalError("db blip")
            return real_get(*args, **kwargs)

        sent, recording_retry = self.retry_headers()
        with patch("apps.videos.tasks.render.VideoClip.objects.get", side_effect=first_read_fails), \
                recording_retry, patch(RENDER, side_effect=self.completing_render) as render:
            result = redelivered_copy(clip.id)

        self.assertTrue(result.successful())
        self.assertEqual(sent, [{RENDER_TAKEOVER_HEADER: TAKEOVER_OBSERVE}])
        render.assert_called_once()  # the retry pinned the dead owner and took over
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)

    def test_a_copy_that_lost_the_clip_never_fails_it_after_the_winner_handed_it_back(self):
        clip = self.make_clip()

        def a_render(clip_id, claim_token=None):
            token_b = take_over(clip_id)
            render_module._release_clip_claim(clip_id, token_b)  # B: transient error, retry pending
            raise RuntimeError("moviepy exploded")  # A fails for good

        with patch(RENDER, side_effect=a_render):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.DRAFT)  # left for B's retry

        with patch(RENDER, side_effect=self.completing_render):
            render_clip_task.apply(args=[str(clip.id)])  # B's retry, a new message

        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)

    def test_the_retry_of_a_delivery_that_claimed_and_handed_back_the_clip_has_no_takeover_right(self):
        clip = self.dead_owner_clip()
        calls = []

        def flaky(clip_id, claim_token=None):
            calls.append(claim_token)
            if len(calls) == 1:
                raise transient_r2_error()
            return self.completing_render(clip_id)

        sent, recording_retry = self.retry_headers()
        with patch(RENDER, side_effect=flaky), recording_retry:
            result = redelivered_copy(clip.id)  # it takes over, then hands the claim back

        self.assertTrue(result.successful())
        self.assertEqual(sent, [{}])
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)

    def test_a_hand_back_that_committed_before_its_driver_raised_still_fails_the_clip(self):
        clip = self.make_clip()
        real_release = render_module._release_clip_claim

        def release_then_raise(clip_id, token):
            real_release(clip_id, token)
            raise OperationalError("connection lost after commit")

        with patch(RENDER, side_effect=OperationalError("x")), \
                patch.object(render_module, "_release_clip_claim", side_effect=release_then_raise):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.failed())
        row = self.clip_row(clip.id)
        self.project.refresh_from_db()
        self.assertEqual(row.status, VideoClip.Status.FAILED)  # not DRAFT forever
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    def test_a_worker_on_the_previous_code_can_still_insert_clips(self):
        # Between the migration and the worker restart, the old model does not
        # know render_unfinished_starts: the database fills its default.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO videos_videoclip (id, project_id, title, start_time, end_time,"
                " virality_score, ai_reasoning, status, created_at)"
                " VALUES (gen_random_uuid(), %s, 'old', 0, 5, 0, '', 'draft', now()) RETURNING id",
                [self.project.pk],
            )
            clip_id = cursor.fetchone()[0]

        row = self.clip_row(clip_id)
        self.assertEqual(row.render_unfinished_starts, 0)
        self.assertIsNone(row.render_claim_token)

    def test_a_copy_that_lost_the_clip_does_not_save_it_and_deletes_its_upload(self):
        clip = self.make_clip()
        tokens = {}

        def upload_while_b_takes_over(path, bucket, key):
            tokens["b"] = take_over(clip.id)

        with self.engine() as r2:
            r2.upload_file.side_effect = upload_while_b_takes_over
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)
        self.assertIsNone(clip.s3_object_key)
        self.assertEqual(clip.render_claim_token, tokens["b"])
        (uploaded,) = self.uploaded_keys()
        r2.delete_object.assert_called_once_with(Bucket="bucket", Key=uploaded)

    def test_a_delivered_clip_has_no_claim_left(self):
        clip = self.make_clip()
        with patch(RENDER, side_effect=WorkerKilled), self.assertRaises(WorkerKilled):
            render_clip_task.apply(args=[str(clip.id)])  # its worker died
        while_rendering = []

        def upload(path, bucket, key):
            row = self.clip_row(clip.id)
            while_rendering.append((row.render_claim_token, row.render_unfinished_starts))

        with self.engine() as r2:
            r2.upload_file.side_effect = upload
            result = redelivered_copy(clip.id)

        self.assertTrue(result.successful())
        ((token, starts),) = while_rendering
        self.assertIsNotNone(token)  # the redelivered copy owned it
        self.assertEqual(starts, 2)  # the dead start and its own
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)
        self.assertEqual(clip.s3_object_key, self.uploaded_keys()[0])
        self.assertIsNone(clip.render_claim_token)
        self.assertEqual(clip.render_unfinished_starts, 0)
        r2.delete_object.assert_not_called()

    def test_two_copies_on_one_host_never_share_the_output_file(self):
        clip = self.make_clip()

        with self.engine():
            render_clip_task.apply(args=[str(clip.id)])
            VideoClip.objects.filter(pk=clip.pk).update(status=VideoClip.Status.DRAFT)
            render_clip_task.apply(args=[str(clip.id)])

        first, second = [call.args[0] for call in self.layout.apply.return_value.write_videofile.call_args_list]
        self.assertNotEqual(first, second)


@override_settings(RENDER_CLIP_MAX_STARTS=3)
class ClipStartCapTests(ClaimHarness, TestCase):
    def test_a_clip_that_keeps_killing_its_worker_fails_at_the_cap(self):
        clip = self.make_clip()

        with patch(RENDER, side_effect=WorkerKilled) as render:
            with self.assertRaises(WorkerKilled):
                render_clip_task.apply(args=[str(clip.id)])
            for _ in range(2):
                with self.assertRaises(WorkerKilled):
                    redelivered_copy(clip.id)
            result = redelivered_copy(clip.id)  # a fourth start would pass the cap

        self.assertEqual(render.call_count, 3)
        self.assertTrue(result.failed())
        clip.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.FAILED)
        self.assertIsNone(clip.render_claim_token)
        self.assertEqual(clip.render_unfinished_starts, 0)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)

    @override_settings(RENDER_CLIP_MAX_STARTS=1)
    def test_the_cap_is_at_least_two(self):
        clip = self.make_clip()
        _claim_clip_for_render(clip.id)  # A renders it; a broker blip redelivers the message

        with patch(RENDER, side_effect=self.completing_render) as render:
            result = redelivered_copy(clip.id)

        # With a cap of 1 the takeover of a live render would fail a healthy clip.
        self.assertTrue(result.successful())
        render.assert_called_once()
        self.assertEqual(self.clip_row(clip.id).status, VideoClip.Status.COMPLETED)

    @override_settings(RENDER_CLIP_MAX_STARTS=2)
    def test_transient_retries_do_not_use_up_the_cap(self):
        clip = self.make_clip()
        calls = []

        def flaky(clip_id, claim_token=None):
            calls.append(claim_token)
            if len(calls) <= RENDER_CLIP_MAX_RETRIES:
                raise transient_r2_error()
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=flaky):
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        self.assertEqual(len(calls), RENDER_CLIP_MAX_RETRIES + 1)
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.COMPLETED)

    @override_settings(RENDER_CLIP_MAX_STARTS=2)
    def test_only_starts_whose_worker_died_count(self):
        clip = self.make_clip()
        calls = []

        def dies_then_flaky(clip_id, claim_token=None):
            calls.append(claim_token)
            if len(calls) == 1:
                raise WorkerKilled()
            if len(calls) <= 1 + RENDER_CLIP_MAX_RETRIES:
                raise transient_r2_error()
            return self.completing_render(clip_id)

        with patch(RENDER, side_effect=dies_then_flaky):
            with self.assertRaises(WorkerKilled):
                render_clip_task.apply(args=[str(clip.id)])
            result = redelivered_copy(clip.id)  # then retried twice

        # Four starts in all, but only one never ended: under the cap of 2.
        self.assertTrue(result.successful())
        self.assertEqual(len(calls), 2 + RENDER_CLIP_MAX_RETRIES)
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, VideoClip.Status.COMPLETED)


class WholeVideoClaimTests(ClaimHarness, TestCase):
    """A whole-video project (single_edit) is paid by its creation reservation."""

    def setUp(self):
        super().setUp()
        self.wallet, _ = Wallet.objects.get_or_create(workspace=self.project.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("100.00"), reserved_balance=0)
        self.txn = reserve_funds(self.wallet.id, Decimal("10.00"))
        VideoProject.objects.filter(pk=self.project.pk).update(
            project_type=VideoProject.Type.SINGLE_EDIT,
            metadata={"priced_duration_s": 10, "reservation_id": str(self.txn.id)},
        )
        self.clip = self.make_clip()

    def txn_status(self):
        self.txn.refresh_from_db()
        return self.txn.status

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def test_the_final_failure_of_the_copy_that_lost_the_clip_does_not_refund_what_the_owner_delivers(self):
        from apps.videos.services.render_engine import RenderEngine

        tokens = {}

        def a_fails_for_good(clip_id, claim_token=None):
            tokens["b"] = take_over(clip_id)
            raise RuntimeError("moviepy exploded")

        with patch(RENDER, side_effect=a_fails_for_good):
            render_clip_task.apply(args=[str(self.clip.id)])

        # A's on_failure left B's clip alone: nothing closed, nothing refunded.
        self.assertEqual(self.clip_row(self.clip.id).status, VideoClip.Status.RENDERING)
        self.assertEqual(self.txn_status(), Transaction.Status.RESERVED)

        # B delivers the video and closes the fan-out, as its task does.
        with self.engine():
            self.assertTrue(RenderEngine.render_clip(self.clip.id, claim_token=tokens["b"]))
        finalize_project_render(self.project.id)

        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(self.clip.id).status, VideoClip.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.txn_status(), Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_a_redelivered_copy_that_fails_an_unpayable_clip_stops_the_live_copy_from_delivering_it(self):
        def project_fails_while_a_uploads(path, bucket, key):
            # The project fails while A renders, and the redelivered copy B
            # finds it unpayable: B fails the clip and the reservation is refunded.
            VideoProject.objects.filter(pk=self.project.pk).update(pipeline_stage=PipelineStage.FAILED)
            redelivered_copy(self.clip.id)

        with self.engine() as r2:
            r2.upload_file.side_effect = project_fails_while_a_uploads
            render_clip_task.apply(args=[str(self.clip.id)])

        clip = self.clip_row(self.clip.id)
        self.assertEqual(clip.status, VideoClip.Status.FAILED)
        self.assertIsNone(clip.s3_object_key)  # no video delivered for free
        (uploaded,) = self.uploaded_keys()
        r2.delete_object.assert_called_once_with(Bucket="bucket", Key=uploaded)
        self.assertEqual(self.txn_status(), Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_the_check_after_the_claim_never_fails_a_clip_another_copy_took_over(self):
        tokens = {}
        answers = []

        def allowed(project_id, clip_id=None):
            answers.append(project_id)
            if len(answers) == 1:
                return True
            tokens["b"] = take_over(self.clip.id)  # B claimed it right after A did
            return False

        with patch(ALLOWED, side_effect=allowed), patch(RENDER) as render:
            render_clip_task.apply(args=[str(self.clip.id)])

        render.assert_not_called()
        clip = self.clip_row(self.clip.id)
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)  # B checks and decides
        self.assertEqual(clip.render_claim_token, tokens["b"])
        self.assertEqual(clip.render_unfinished_starts, 1)

    def test_a_last_attempt_that_fails_before_the_claim_is_refunded(self):
        with patch(ALLOWED, side_effect=OperationalError("gone")), patch(RENDER) as render:
            result = render_clip_task.apply(args=[str(self.clip.id)])

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(self.clip.id).status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)
        self.assertEqual(self.txn_status(), Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_redelivered_copy_that_fails_before_the_claim_refunds_a_dead_owners_video(self):
        with patch(RENDER, side_effect=WorkerKilled), self.assertRaises(WorkerKilled):
            render_clip_task.apply(args=[str(self.clip.id)])  # A's worker died

        with patch(ALLOWED, side_effect=RuntimeError("boom")), patch(RENDER) as render:
            result = redelivered_copy(self.clip.id)

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.clip_row(self.clip.id).status, VideoClip.Status.FAILED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)
        self.assertEqual(self.txn_status(), Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_queued_copy_of_an_unpayable_project_leaves_a_live_claim_alone(self):
        _claim_clip_for_render(self.clip.id)  # a live delivery is rendering it
        owner = self.clip_row(self.clip.id).render_claim_token
        VideoProject.objects.filter(pk=self.project.pk).update(pipeline_stage=PipelineStage.FAILED)

        with patch(RENDER) as render:
            render_clip_task.apply(args=[str(self.clip.id)])  # not a redelivery

        render.assert_not_called()
        clip = self.clip_row(self.clip.id)
        self.assertEqual(clip.status, VideoClip.Status.RENDERING)
        self.assertEqual(clip.render_claim_token, owner)
        self.assertEqual(self.txn_status(), Transaction.Status.RESERVED)  # waits for that render
