"""
A task that re-queues itself with apply_async (a busy project lock, a prompt
parser retry, a capacity wait) used to end with the broker's error when the
re-queue could not be published. The tasks ack late, and Celery acks a task
that raised, so the only message of the project was lost. Now the same
message goes back to the broker (reject with requeue) after a short pause,
with the counters it carries untouched and nothing of the lost re-queue kept.

Each test runs the message as a worker does (celery.worker.request.Request)
and checks what the broker is told: ack or reject with requeue.
"""

from unittest.mock import Mock, patch

from celery.contrib.testing.mocks import TaskMessage
from django.contrib.auth import get_user_model
from django.test import TestCase
from kombu.exceptions import OperationalError

from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.tasks import _merge_metadata, process_initial_ingestion, render_video_segments
from apps.videos.tasks.ingestion import _restore_capacity_wait
from apps.videos.tasks.render import RENDER_LOCK_RETRIES_HEADER
from apps.videos.tasks.shared import REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import REDIS_CLIENT
from apps.videos.tests.test_capacity_pipeline import CapacityHarness

User = get_user_model()

PAUSE = "apps.videos.tasks.shared.time.sleep"
APPLY_PROMPT = "apps.videos.services.ai.prompt_validator.apply_prompt_to_project"
BROKER_DOWN = OperationalError("Error 111 connecting to secret-host")


class InlinePool:
    """The worker pool, in-process: runs the traced task and hands the outcome to the request."""

    def apply_async(self, target, args=(), callback=None, **kwargs):
        callback(target(*args))


def run_on_worker(task, message):
    """Runs one message as a worker does (acks_late) and returns its (ack, reject) callbacks."""
    from celery.worker.request import Request

    from backend.celery import app

    ack, reject = Mock(name="ack"), Mock(name="reject")
    Request(message, app=app, task=task, on_ack=ack, on_reject=reject).execute_using_pool(InlinePool())
    return ack, reject


class RedeliveryAssertions:
    def run_with_broker_down(self, task, message):
        with patch(PAUSE) as pause, self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            ack, reject = run_on_worker(task, message)
        return ack, reject, pause, "\n".join(logs.output)

    def assert_redelivered(self, ack, reject, pause):
        ack.assert_not_called()
        reject.assert_called_once()
        self.assertIs(reject.call_args.args[2], True)  # requeue
        pause.assert_called_once_with(REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS)

    def assert_logged(self, logs, event, project, kind):
        self.assertIn(
            f"{event} project_id={project.id} requeue={kind} error=OperationalError", logs,
        )
        self.assertNotIn("secret-host", logs)


class LockBusyRequeueNotPublishedTests(RedeliveryAssertions, TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patcher = patch(REDIS_CLIENT, return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        user = User.objects.create_user(username="busy", email="busy@test.com", password="pw")
        workspace = Workspace.objects.create(name="Busy", owner=user)
        self.project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="Busy lock",
            status=VideoProject.Status.RENDERING,
        )
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))  # a live worker holds the project

    def test_a_paper_edit_requeue_that_is_not_published_is_redelivered(self):
        message = TaskMessage(
            render_video_segments.name, args=[str(self.project.id)], **{RENDER_LOCK_RETRIES_HEADER: 2},
        )

        with patch.object(render_video_segments, "apply_async", side_effect=BROKER_DOWN):
            ack, reject, pause, logs = self.run_with_broker_down(render_video_segments, message)

        self.assert_redelivered(ack, reject, pause)
        self.assert_logged(logs, "render.requeue_not_published", self.project, "lock")

    def test_an_ingestion_requeue_that_is_not_published_is_redelivered(self):
        message = TaskMessage(
            process_initial_ingestion.name, args=[str(self.project.id)], kwargs={"lock_retries": 2},
        )

        with patch.object(process_initial_ingestion, "apply_async", side_effect=BROKER_DOWN):
            ack, reject, pause, logs = self.run_with_broker_down(process_initial_ingestion, message)

        self.assert_redelivered(ack, reject, pause)
        self.assert_logged(logs, "pipeline.requeue_not_published", self.project, "lock")

    def test_a_published_requeue_still_acks_the_message(self):
        message = TaskMessage(render_video_segments.name, args=[str(self.project.id)])
        with patch.object(render_video_segments, "apply_async") as requeue, patch(PAUSE) as pause:
            ack, reject = run_on_worker(render_video_segments, message)

        requeue.assert_called_once()
        ack.assert_called_once()
        reject.assert_not_called()
        pause.assert_not_called()


class IngestionRequeueNotPublishedTests(RedeliveryAssertions, CapacityHarness, TestCase):
    """The prompt parser's re-queue and the capacity wait's re-queue."""

    def ingestion_message(self, project, **kwargs):
        return TaskMessage(process_initial_ingestion.name, args=[str(project.id)], kwargs=kwargs)

    def assert_no_failure_recorded(self, project):
        project.refresh_from_db()
        self.assertEqual(project.pipeline_attempts, 0)
        self.assertFalse(project.pipeline_error_code)
        self.assertNotEqual(project.status, VideoProject.Status.FAILED)

    def test_a_prompt_parse_requeue_that_is_not_published_is_redelivered(self):
        from apps.videos.services.ai.prompt_validator import PromptParseRetry

        project = self.make_project()
        self.requeue.side_effect = BROKER_DOWN
        message = self.ingestion_message(project, lock_retries=1, parse_retries=1)

        with patch(APPLY_PROMPT, side_effect=PromptParseRetry()):
            ack, reject, pause, logs = self.run_with_broker_down(process_initial_ingestion, message)

        self.assert_redelivered(ack, reject, pause)
        self.assert_logged(logs, "pipeline.requeue_not_published", project, "prompt_parse")
        # The lost re-queue would have spent a parser retry; the message that
        # comes back still carries parse_retries=1.
        self.assertEqual(
            self.requeue.call_args.kwargs["kwargs"], {"lock_retries": 1, "parse_retries": 2},
        )
        self.assert_no_failure_recorded(project)

    def test_a_capacity_requeue_that_is_not_published_is_redelivered_without_counting_its_wait(self):
        project = self.make_project()
        self.store.denials = [30.0, 30.0]
        self.run_task(project)  # a first wait, re-queued normally
        project.refresh_from_db()
        planned = project.metadata["capacity_wait"]
        self.assertGreater(planned["planned_s"], 0)
        self.requeue.side_effect = BROKER_DOWN

        ack, reject, pause, logs = self.run_with_broker_down(
            process_initial_ingestion, self.ingestion_message(project),
        )

        self.assert_redelivered(ack, reject, pause)
        self.assert_logged(logs, "pipeline.requeue_not_published", project, "capacity")
        project.refresh_from_db()
        # The second wait was taken back: the redelivery plans it again, once.
        self.assertEqual(project.metadata["capacity_wait"], planned)
        self.assertEqual(project.pipeline_stage_status["transcribed"], "pending_capacity")
        self.assert_no_failure_recorded(project)

    def test_a_first_capacity_wait_that_is_not_published_leaves_no_wait(self):
        project = self.make_project()
        self.store.denials = [30.0]
        self.requeue.side_effect = BROKER_DOWN

        ack, reject, pause, _ = self.run_with_broker_down(
            process_initial_ingestion, self.ingestion_message(project),
        )

        self.assert_redelivered(ack, reject, pause)
        project.refresh_from_db()
        self.assertIsNone(project.metadata.get("capacity_wait"))
        self.assert_no_failure_recorded(project)

    def test_the_wait_is_not_taken_back_once_another_delivery_changed_it(self):
        project = self.make_project()
        written = {"stage": "transcribed", "since": "2026-11-24T00:00:00+00:00", "planned_s": 60.0, "attempts": 0}
        newer = {**written, "planned_s": 95.0}
        _merge_metadata(project.id, {"capacity_wait": newer})

        _restore_capacity_wait(project.id, written, None)

        project.refresh_from_db()
        self.assertEqual(project.metadata["capacity_wait"], newer)
