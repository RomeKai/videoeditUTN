"""
The «Renderizar» step of the process timeline has its start and its duration
(``stage_timings['render_dispatched']``): the start is written when the render
is dispatched (the approval of the review, the auto-render, a render asked for
later and a re-dispatch) and the end by ``finalize_project_render``.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage, mark_failed
from apps.videos.services.project_summary import stage_timings
from apps.videos.tasks import finalize_project_render
from apps.videos.tasks.ingestion import _metadata_with_render_finished, _metadata_with_render_started
from apps.videos.tests.fakes import CapturedTaskFailures
from apps.videos.tests.test_approve_per_clip import ApprovalHarness
from apps.videos.tests.test_clip_actions import DELETE_OBJECT, ClipActionsHarness, clip_url, project_url
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result

RENDER = "render_dispatched"
DONE = VideoClip.Status.COMPLETED


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

    def test_a_clip_render_starts_the_step_again_and_its_close_ends_it(self):
        project, (clip, _other) = self.rendered_before()

        response, _delay = self.post(clip_url(clip, "render"))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assert_new_render_running(project)

        started_ago(project, 20)
        with patch(DELETE_OBJECT):
            self.render_with(clip)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        duration = stage_timings(project)[RENDER]["duration_s"]
        self.assertGreaterEqual(duration, 20)
        self.assertLess(duration, 40)  # never the 40 s of the render before

    def test_a_retry_and_a_rerender_start_it_again_too(self):
        project, (_done, failed) = self.rendered_before((DONE, VideoClip.Status.FAILED))
        response, _delay = self.post(clip_url(failed, "retry"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assert_new_render_running(project)

        project, _clips = self.rendered_before()
        response, _delay = self.post(project_url(project, "rerender"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assert_new_render_running(project)

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

    def test_a_render_that_fails_and_gives_the_clip_back_is_closed_with_its_own_times(self):
        project, (clip, _other) = self.rendered_before()
        self.post(clip_url(clip, "render"))
        started_ago(project, 15)

        self.assertTrue(self.render_with(clip, side_effect=RuntimeError("bad encode")).failed())

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        duration = stage_timings(project)[RENDER]["duration_s"]
        self.assertGreaterEqual(duration, 15)
        self.assertLess(duration, 40)
