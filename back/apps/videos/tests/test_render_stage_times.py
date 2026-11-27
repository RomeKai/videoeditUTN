"""
The «Renderizar» step of the process timeline has its start and its duration
(``stage_timings['render_dispatched']``): the start is written when the render
is dispatched and the end by ``finalize_project_render``. Only a render of the
project is one: the approval of the review, the auto-render, a whole video,
«renderizar todo de nuevo» and a re-dispatch of one of them. The render of one
clip (its editor, a retry) leaves those times as they are.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.payments.services.wallet_service import reserve_funds
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage, mark_failed
from apps.videos.services.project_summary import stage_timings
from apps.videos.tasks import finalize_project_render
from apps.videos.tasks.ingestion import (
    _metadata_for_render_dispatch, _metadata_with_render_finished, _metadata_with_render_started,
)
from apps.videos.tests.fakes import CapturedTaskFailures
from apps.videos.tests.test_approve_per_clip import ApprovalHarness
from apps.videos.tests.test_clip_actions import DELETE_OBJECT, ClipActionsHarness, clip_url, project_url
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result

RENDER = "render_dispatched"
DONE = VideoClip.Status.COMPLETED


def project_scope(project):
    project.refresh_from_db()
    return project.metadata.get("clip_render_scope")


def started_ago(project, seconds):
    """Moves the start of the render ``seconds`` back, as if it had been rendering that long."""
    project.refresh_from_db()
    started = datetime.fromisoformat(project.metadata["stage_started_at"][RENDER]) - timedelta(seconds=seconds)
    project.metadata["stage_started_at"][RENDER] = started.isoformat()
    VideoProject.objects.filter(pk=project.pk).update(metadata=project.metadata)
    return started


class RenderTimesHelpersTests(SimpleTestCase):
    def test_a_start_drops_the_times_of_the_render_before(self):
        now = timezone.now()
        old = {
            "other": 1,
            "stage_started_at": {"transcribed": "t", RENDER: "2020-01-01T00:00:00+00:00"},
            "stage_durations": {"transcribed": 3.0, RENDER: 40.0},
            "stage_finished_at": {"transcribed": "f", RENDER: "2020-01-01T00:00:40+00:00"},
        }

        fresh = _metadata_with_render_started(old, now)

        self.assertEqual(fresh, {
            "other": 1,
            "stage_started_at": {"transcribed": "t", RENDER: now.isoformat()},
            "stage_durations": {"transcribed": 3.0},
            "stage_finished_at": {"transcribed": "f"},
        })
        self.assertEqual(old["stage_durations"][RENDER], 40.0)  # the caller's dict is not touched
        self.assertEqual(_metadata_with_render_started(None, now), {"stage_started_at": {RENDER: now.isoformat()}})

    def test_only_a_render_of_the_project_starts_the_step_again(self):
        now = timezone.now()
        closed = {
            "stage_started_at": {RENDER: "2020-01-01T00:00:00+00:00"},
            "stage_durations": {RENDER: 40.0},
            "stage_finished_at": {RENDER: "2020-01-01T00:00:40+00:00"},
        }

        # One clip: the times of the project's render stay, in a dict of its own.
        kept = _metadata_for_render_dispatch(closed, False, now)
        self.assertEqual(kept, closed)
        self.assertIsNot(kept, closed)
        # The project: a new start and nothing of the render before.
        self.assertEqual(_metadata_for_render_dispatch(closed, True, now), _metadata_with_render_started(closed, now))
        # One clip, with no closed render to keep: it starts the step, or its close would measure the old start.
        for unclosed in ({}, None, {"stage_started_at": {RENDER: "2020-01-01T00:00:00+00:00"}}):
            self.assertEqual(
                _metadata_for_render_dispatch(unclosed, False, now)["stage_started_at"][RENDER], now.isoformat(),
            )
        # A duration alone (written before the ends were) is a closed render too.
        only_duration = {"stage_durations": {RENDER: 40.0}}
        self.assertEqual(_metadata_for_render_dispatch(only_duration, False, now), only_duration)

    def test_the_end_is_measured_from_the_start(self):
        now = timezone.now()
        metadata = {"stage_started_at": {RENDER: (now - timedelta(seconds=42.5)).isoformat()}, "stage_durations": {"x": 1}}

        fresh = _metadata_with_render_finished(metadata, now)

        self.assertEqual(fresh["stage_durations"], {"x": 1, RENDER: 42.5})
        self.assertEqual(fresh["stage_finished_at"], {RENDER: now.isoformat()})

    def test_without_a_start_it_can_read_nothing_is_made_up(self):
        now = timezone.now()
        for started in (None, {}, {RENDER: None}, {RENDER: "ayer"}, {RENDER: 5}, "x",
                        {RENDER: "2026-01-01T00:00:00"},  # naive
                        {RENDER: (now + timedelta(hours=1)).isoformat()}):
            metadata = {"stage_started_at": started}
            self.assertIs(_metadata_with_render_finished(metadata, now), metadata, started)
        self.assertIsNone(_metadata_with_render_finished(None, now))

    def test_a_start_that_already_has_its_end_is_of_an_earlier_render_and_measures_nothing(self):
        # Every start deletes the end of the render before: with an end, the start is stale.
        now = timezone.now()
        week_ago = now - timedelta(days=7)
        metadata = {
            "stage_started_at": {RENDER: week_ago.isoformat()},
            "stage_durations": {RENDER: 0.012},
            "stage_finished_at": {RENDER: (week_ago + timedelta(seconds=0.012)).isoformat()},
        }

        self.assertIs(_metadata_with_render_finished(metadata, now), metadata)
        # An end alone (no duration) is enough to know.
        del metadata["stage_durations"]
        self.assertIs(_metadata_with_render_finished(metadata, now), metadata)


class ApprovedRenderTimesTests(CapturedTaskFailures, ApprovalHarness, APITestCase):
    def approve_two(self):
        first, second, _left_out = self.suggested
        self.approve_ok([
            {"start": 0, "end": 5, "clip_id": str(first.id)}, {"start": 10, "end": 15, "clip_id": str(second.id)},
        ])
        return self.clips()

    def test_the_approval_writes_the_start_and_the_close_the_duration(self):
        before = timezone.now()
        clips = self.approve_two()

        timing = stage_timings(self.project)[RENDER]
        self.assertGreaterEqual(datetime.fromisoformat(timing["started_at"]), before)
        self.assertEqual((timing["finished_at"], timing["duration_s"]), (None, None))

        started = started_ago(self.project, 90)
        VideoClip.objects.filter(pk=clips[0].pk).update(status=DONE)
        # A clip is still to render: nothing ends yet.
        self.assertIsNone(finalize_project_render(self.project.id))
        self.project.refresh_from_db()
        self.assertNotIn(RENDER, self.project.metadata.get("stage_durations", {}))

        VideoClip.objects.filter(pk=clips[1].pk).update(status=DONE)
        self.assertEqual(finalize_project_render(self.project.id), PipelineStage.COMPLETED)

        self.project.refresh_from_db()
        timing = stage_timings(self.project)[RENDER]
        self.assertGreaterEqual(timing["duration_s"], 90)
        self.assertLess(timing["duration_s"], 150)
        # The start the timeline shows is the end minus the duration (rounded to the millisecond).
        self.assertLess(abs(datetime.fromisoformat(timing["started_at"]) - started), timedelta(seconds=1))
        self.assertEqual(timing["finished_at"], self.project.metadata["stage_finished_at"][RENDER])
        # The approval's own keys are still there: one write, nothing lost.
        self.assertEqual(self.project.metadata["render_clip_ids"], [str(clip.pk) for clip in clips])

        # A second close changes nothing.
        saved = dict(self.project.metadata)
        finalize_project_render(self.project.id)
        self.project.refresh_from_db()
        self.assertEqual(self.project.metadata["stage_durations"], saved["stage_durations"])
        self.assertEqual(self.project.metadata["stage_finished_at"], saved["stage_finished_at"])

    def test_a_render_that_ends_partial_has_its_times_too(self):
        clips = self.approve_two()
        started_ago(self.project, 30)
        VideoClip.objects.filter(pk=clips[0].pk).update(status=DONE)
        VideoClip.objects.filter(pk=clips[1].pk).update(status=VideoClip.Status.FAILED)

        self.assertEqual(finalize_project_render(self.project.id), PipelineStage.PARTIAL)

        self.project.refresh_from_db()
        self.assertGreaterEqual(stage_timings(self.project)[RENDER]["duration_s"], 30)

    def test_the_approval_does_not_keep_a_start_an_earlier_dispatch_left(self):
        stale = "2020-01-01T00:00:00+00:00"
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={"stage_started_at": {RENDER: stale}})

        self.approve_two()

        self.assertNotEqual(self.project.metadata["stage_started_at"][RENDER], stale)


class AutoRenderTimesTests(IngestionHarness, TestCase):
    def setUp(self):
        super().setUp()
        fund_wallet(self.workspace, "100.00")
        self.mock_select.return_value = selection_result(count=2)

    def run_to_render(self, project, task=None):
        with self.captureOnCommitCallbacks(execute=True):
            (task or self.run_task)(project)
        project.refresh_from_db()
        return project

    def test_the_dispatch_is_the_start_and_not_the_end_of_the_render(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        timing = stage_timings(project)[RENDER]
        self.assertIsNotNone(timing["started_at"])
        # Dispatching took milliseconds: that is not how long the render took.
        self.assertEqual((timing["finished_at"], timing["duration_s"]), (None, None))
        self.assertNotIn(RENDER, project.metadata["stage_durations"])
        # The stages before it keep theirs.
        self.assertIn("clips_selected", project.metadata["stage_durations"])

        started_ago(project, 60)
        VideoClip.objects.filter(project=project).update(status=DONE)
        finalize_project_render(project.id)

        project.refresh_from_db()
        self.assertGreaterEqual(stage_timings(project)[RENDER]["duration_s"], 60)

    def test_a_redispatch_at_the_render_writes_its_own_start(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        stale = started_ago(project, 3 * 24 * 3600)
        self.assertTrue(mark_failed(project.id))

        project = self.run_to_render(project, task=self.redispatch_task)

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        started = datetime.fromisoformat(project.metadata["stage_started_at"][RENDER])
        self.assertGreater(started, stale + timedelta(days=2))
        self.assertNotIn(RENDER, project.metadata["stage_durations"])


    def closed_render_reopened_for_one_render(self, scope):
        """
        An auto-rendered project that closed (60 s), then a render the user
        paid for (``scope``) that left it failed at render_dispatched with its
        reservation still RESERVED: what a re-dispatch resumes.
        """
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        started_ago(project, 60)
        VideoClip.objects.filter(project=project).update(status=DONE)
        self.assertEqual(finalize_project_render(project.id), PipelineStage.COMPLETED)
        project.refresh_from_db()
        closed = {key: project.metadata[key][RENDER] for key in ("stage_started_at", "stage_durations", "stage_finished_at")}
        clip = VideoClip.objects.filter(project=project).first()
        VideoClip.objects.filter(pk=clip.pk).update(status=VideoClip.Status.DRAFT)
        wallet = fund_wallet(self.workspace, "100.00")
        paid = reserve_funds(wallet.id, Decimal("5.00"), project=project)
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.RENDERING, pipeline_stage=PipelineStage.RENDER_DISPATCHED,
            metadata={
                **project.metadata, "clip_render_reservation_id": str(paid.id), "clip_render_clip_ids": [str(clip.pk)],
                "clip_render_seconds": 5.0, "clip_render_scope": scope,
            },
        )
        self.assertTrue(mark_failed(project.id))
        return project, closed

    def times(self, project):
        return {key: project.metadata[key].get(RENDER) for key in ("stage_started_at", "stage_durations", "stage_finished_at")}

    def test_a_redispatch_that_resumes_the_render_of_one_clip_keeps_the_times_of_the_projects_render(self):
        project, closed = self.closed_render_reopened_for_one_render("clip")

        project = self.run_to_render(project, task=self.redispatch_task)

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(self.times(project), closed)
        self.assertEqual(stage_timings(project)[RENDER]["duration_s"], closed["stage_durations"])

    def test_a_redispatch_that_resumes_a_render_of_every_clip_starts_the_step_again(self):
        project, closed = self.closed_render_reopened_for_one_render("project")

        project = self.run_to_render(project, task=self.redispatch_task)

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        times = self.times(project)
        self.assertGreater(times["stage_started_at"], closed["stage_finished_at"])
        self.assertEqual((times["stage_durations"], times["stage_finished_at"]), (None, None))


class PaidRenderTimesTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    OLD = {
        "stage_started_at": {RENDER: "2020-01-01T00:00:00+00:00", "transcribed": "2019-12-31T23:00:00+00:00"},
        "stage_durations": {RENDER: 40.0, "transcribed": 12.0},
        "stage_finished_at": {RENDER: "2020-01-01T00:00:40+00:00", "transcribed": "2019-12-31T23:00:12+00:00"},
    }

    def rendered_before(self, statuses=(DONE, DONE)):
        project, clips = self.finished(statuses)
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, **self.OLD})
        project.refresh_from_db()
        return project, clips

    def assert_new_render_running(self, project):
        project.refresh_from_db()
        timing = stage_timings(project)[RENDER]
        self.assertGreater(datetime.fromisoformat(timing["started_at"]), timezone.now() - timedelta(minutes=5))
        self.assertEqual((timing["finished_at"], timing["duration_s"]), (None, None))
        # Only the render's: the other stages keep their times.
        self.assertEqual(project.metadata["stage_durations"], {"transcribed": 12.0})
        self.assertEqual(project.metadata["stage_finished_at"], {"transcribed": self.OLD["stage_finished_at"]["transcribed"]})
        self.assertEqual(project.metadata["stage_started_at"]["transcribed"], self.OLD["stage_started_at"]["transcribed"])

    def assert_times_of_the_render_before(self, project):
        """The step still describes the project's render: the dispatch of one clip wrote nothing on it."""
        project.refresh_from_db()
        for key in ("stage_started_at", "stage_durations", "stage_finished_at"):
            self.assertEqual(project.metadata[key], self.OLD[key], key)
        timing = stage_timings(project)[RENDER]
        self.assertEqual((timing["finished_at"], timing["duration_s"]), ("2020-01-01T00:00:40+00:00", 40.0))

    def test_a_clip_render_leaves_the_times_of_the_projects_render(self):
        project, (clip, _other) = self.rendered_before()

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(project_scope(project), "clip")
        # While it renders, and once it is delivered: «Renderizar» says 0:40, as before.
        self.assert_times_of_the_render_before(project)
        with patch(DELETE_OBJECT):
            self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        self.assert_times_of_the_render_before(project)

    def test_a_retry_leaves_them_too(self):
        project, (_done, failed) = self.rendered_before((DONE, VideoClip.Status.FAILED))

        response, _delay = self.post(clip_url(failed, "retry"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assert_times_of_the_render_before(project)
        self.render_with(failed)
        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        self.assert_times_of_the_render_before(project)

    def test_a_clip_render_that_fails_leaves_them_too(self):
        project, (clip, _other) = self.rendered_before()
        self.post(clip_url(clip, "render"))

        self.assertTrue(self.render_with(clip, side_effect=RuntimeError("bad encode")).failed())

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        self.assert_times_of_the_render_before(project)

    def test_rendering_every_clip_again_starts_the_step_again_and_its_close_ends_it(self):
        project, clips = self.rendered_before()

        response, _delay = self.post(project_url(project, "rerender"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(project_scope(project), "project")
        self.assert_new_render_running(project)

        started_ago(project, 20)
        with patch(DELETE_OBJECT):
            for clip in clips:
                self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        duration = stage_timings(project)[RENDER]["duration_s"]
        self.assertGreaterEqual(duration, 20)
        self.assertLess(duration, 40)  # never the 40 s of the render before

    def test_the_clip_of_a_whole_video_is_the_project_so_its_render_starts_the_step_again(self):
        for action in ("render", "retry"):
            with self.subTest(action=action):
                project, clip = self.whole_video(
                    **({"clip_status": VideoClip.Status.FAILED, "stage": PipelineStage.PARTIAL, "creation": "refunded"}
                       if action == "retry" else {}),
                )
                VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, **self.OLD})

                response, _delay = self.post(clip_url(clip, action))

                self.assertEqual(response.status_code, 202, response.content[:300])
                self.assertEqual(project_scope(project), "project")
                self.assert_new_render_running(project)

    def test_a_clip_render_over_a_render_that_never_closed_writes_its_own_times(self):
        # The project's render left a start and no end (it failed before the starts had an end,
        # or the close never ran): there is nothing to keep, and closing that start with this
        # render would measure days.
        project, (clip, _other) = self.finished((DONE, DONE))
        week_ago = timezone.now() - timedelta(days=7)
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**project.metadata, "stage_started_at": {RENDER: week_ago.isoformat()}},
        )

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        project.refresh_from_db()
        started = datetime.fromisoformat(project.metadata["stage_started_at"][RENDER])
        self.assertGreater(started, week_ago + timedelta(days=6))
        with patch(DELETE_OBJECT):
            self.render_with(clip)
        project.refresh_from_db()
        self.assertLess(stage_timings(project)[RENDER]["duration_s"], 3600)

    def test_a_render_reserved_before_the_starts_were_written_is_not_measured_from_an_old_dispatch(self):
        # What the code before left on an auto-rendered project: the start of its dispatch, days
        # ago, with the milliseconds the dispatch took. A clip render reserved by that code (it
        # wrote no start) closes with this one.
        project, (clip, _other) = self.finished((DONE, DONE))
        week_ago = timezone.now() - timedelta(days=7)
        old = {
            "stage_started_at": {RENDER: week_ago.isoformat()},
            "stage_durations": {RENDER: 0.012},
            "stage_finished_at": {RENDER: (week_ago + timedelta(seconds=0.012)).isoformat()},
        }
        with patch("apps.videos.views_clips._metadata_with_render_started", side_effect=lambda metadata: metadata, create=True), \
                patch("apps.videos.tasks.ingestion._metadata_with_render_started", side_effect=lambda metadata, now=None: metadata):
            VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, **old})
            response, _delay = self.post(clip_url(clip, "render"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        project.refresh_from_db()
        self.assertEqual(project.metadata["stage_started_at"], old["stage_started_at"])  # as that code left it

        with patch(DELETE_OBJECT):
            self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        # Never the week since that dispatch.
        self.assertEqual(project.metadata["stage_durations"], old["stage_durations"])
        self.assertEqual(project.metadata["stage_finished_at"], old["stage_finished_at"])
