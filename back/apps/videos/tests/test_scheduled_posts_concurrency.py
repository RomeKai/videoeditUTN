"""
Gap 8: the writes of /api/v1/scheduled-posts/ against the publisher, at the
same time, with real threads and real row locks (services/scheduled_posts.py).

The dispatcher (dispatch_scheduled_posts_batch) and the worker
(upload_to_social_network) are the real ones: each test pauses one of them
while it holds the post's row lock, sends the request, checks that the request
waits for that lock, and then lets the publisher go on. Without the lock, the
request would decide on what it read before and answer as if nothing happened.
"""
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services import scheduled_posts as service
from apps.videos.tasks import dispatch_scheduled_posts_batch, upload_to_social_network

User = get_user_model()
Status = ScheduledPost.Status

SEND = "apps.integrations.ayrshare_api.AyrshareClient.send_post"
PRESIGN = "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url"
UPLOAD = "apps.videos.tasks.upload_to_social_network.apply_async"
SEO_DELAY = "apps.videos.tasks.process_video_seo.delay"


class PostRacesHarness(APITransactionTestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", email="alice@test.com", password="pw")
        self.home = self.alice.owned_workspaces.get()
        Workspace.objects.filter(pk=self.home.pk).update(
            # Its own plan: a TransactionTestCase empties the tables, the seeded plans too.
            subscription_plan=SubscriptionPlan.objects.create(
                name="Pro de carrera", allow_scheduling=True, allow_crossposting=True, max_video_duration_seconds=3600,
            ),
            ayrshare_profile_key="perfil",
        )
        project = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title="Entrevista", source_file="videos/raw/a.mp4",
        )
        self.clip = VideoClip.objects.create(
            project=project, title="Clip", start_time=0, end_time=30, status=VideoClip.Status.COMPLETED,
            s3_object_key="clips/a.mp4",
        )
        caches["locks"].clear()
        self.addCleanup(caches["locks"].clear)

    def make_post(self, status, publish_at=None):
        return ScheduledPost.objects.create(
            video_clip=self.clip, platform=ScheduledPost.Platform.INSTAGRAM_REELS, status=status,
            publish_at=publish_at or timezone.now() + timedelta(minutes=10),
        )

    def request(self, method, url, body=None):
        """Runs in its own thread: its own connection, closed at the end."""
        try:
            api = APIClient()
            api.force_authenticate(self.alice)
            return getattr(api, method)(url, body or {}, format="json", HTTP_X_WORKSPACE_ID=str(self.home.pk))
        finally:
            connection.close()

    def in_thread(self, target, *args):
        result = {}

        def run():
            try:
                result["value"] = target(*args)
            except BaseException as exc:  # reported by the test, not lost in the thread
                result["error"] = exc
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        thread.start()
        return thread, result

    def wait_until_someone_waits_for_a_lock(self, timeout=10):
        """Until a connection of this test database is waiting for a row lock."""
        deadline = time.monotonic() + timeout
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return
                time.sleep(0.02)
        self.fail("the request never waited for the post's row lock")

    def pause_in_save(self, status):
        """Patches ScheduledPost.save: the save that sets ``status`` waits for ``release`` inside its transaction."""
        taken, release = threading.Event(), threading.Event()
        real_save = ScheduledPost.save

        def save(post, *args, **kwargs):
            real_save(post, *args, **kwargs)
            if post.status == status and threading.current_thread() is not threading.main_thread() and not taken.is_set():
                taken.set()
                release.wait(15)

        return patch.object(ScheduledPost, "save", save), taken, release

    @staticmethod
    def network():
        """Ayrshare, answering only once ``sent`` is set: the request in the test decides first."""
        sent = threading.Event()

        def answer(**kwargs):
            sent.wait(15)
            return {"id": "ayr-1"}

        return sent, patch(SEND, side_effect=answer)

    @staticmethod
    def finish(*threads):
        for thread in threads:
            thread.join(timeout=30)


class CancelRacesTests(PostRacesHarness):
    def test_a_cancel_waits_for_the_worker_and_never_deletes_a_post_it_is_sending(self):
        post = self.make_post(Status.QUEUED, publish_at=timezone.now())
        paused, taken, release = self.pause_in_save(Status.PROCESSING)
        sent, send = self.network()
        with paused, patch(PRESIGN, return_value="https://media.test/a.mp4"), send as send:
            worker, worked = self.in_thread(lambda: upload_to_social_network.apply(args=[post.pk]).get())
            self.assertTrue(taken.wait(10), "the worker never took the post")
            cancel, cancelled = self.in_thread(self.request, "post", reverse("scheduled-post-cancel", args=[post.pk]))
            self.wait_until_someone_waits_for_a_lock()
            release.set()
            self.finish(cancel)
            sent.set()  # the network answers once the cancel did
            self.finish(worker)

        self.assertNotIn("error", worked)
        self.assertEqual(worked["value"], "Published via Ayrshare: instagram")
        response = cancelled["value"]
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PUBLISHED)  # still there: what went out is on record
        send.assert_called_once()

    def test_a_cancel_after_the_dispatcher_queued_the_post_leaves_its_task_nothing_to_publish(self):
        post = self.make_post(Status.SCHEDULED)
        taken, release = threading.Event(), threading.Event()

        def queued(*args, **kwargs):
            # Inside the dispatcher's transaction, with the post's row lock held.
            taken.set()
            release.wait(15)

        with patch(UPLOAD, side_effect=queued) as upload:
            dispatcher, dispatched = self.in_thread(dispatch_scheduled_posts_batch)
            self.assertTrue(taken.wait(10), "the dispatcher never queued the post")
            cancel, cancelled = self.in_thread(self.request, "post", reverse("scheduled-post-cancel", args=[post.pk]))
            self.wait_until_someone_waits_for_a_lock()
            release.set()
            self.finish(dispatcher, cancel)

        self.assertEqual(dispatched["value"], "Queued 1 posts.")
        upload.assert_called_once()
        self.assertEqual(cancelled["value"].status_code, 204)
        with patch(SEND) as send:
            self.assertEqual(upload_to_social_network.apply(args=[post.pk]).get(), "Post Deleted")
        send.assert_not_called()


class EditRacesTests(PostRacesHarness):
    def test_an_edit_waits_for_the_dispatcher_and_refuses_a_post_it_queued(self):
        post = self.make_post(Status.SCHEDULED)
        planned = post.publish_at
        taken, release = threading.Event(), threading.Event()

        def queued(*args, **kwargs):
            taken.set()
            release.wait(15)

        with patch(UPLOAD, side_effect=queued):
            dispatcher, _ = self.in_thread(dispatch_scheduled_posts_batch)
            self.assertTrue(taken.wait(10), "the dispatcher never queued the post")
            later = (timezone.now() + timedelta(days=3)).isoformat()
            edit, edited = self.in_thread(
                self.request, "patch", reverse("scheduled-post-detail", args=[post.pk]),
                {"publish_at": later, "caption": "Otro texto"},
            )
            self.wait_until_someone_waits_for_a_lock()
            release.set()
            self.finish(dispatcher, edit)

        response = edited["value"]
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))
        post.refresh_from_db()
        # Its task left with the old time: the post keeps it, and its text.
        self.assertEqual((post.status, post.publish_at, post.caption), (Status.QUEUED, planned, ""))


class RetryRacesTests(PostRacesHarness):
    def test_two_retries_at_once_queue_the_post_once(self):
        post = self.make_post(Status.FAILED, publish_at=timezone.now() - timedelta(hours=1))
        meeting = threading.Barrier(2)
        lock_post = service._lock_post

        def lock_then_meet(*args, **kwargs):
            locked = lock_post(*args, **kwargs)
            # With the row lock the second request never gets here while the first waits,
            # so the first goes on alone after two seconds.
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return locked

        url = reverse("scheduled-post-retry", args=[post.pk])
        with patch.object(service, "_lock_post", lock_then_meet):
            first, one = self.in_thread(self.request, "post", url)
            second, other = self.in_thread(self.request, "post", url)
            self.finish(first, second)

        answers = sorted(result["value"].status_code for result in (one, other))
        self.assertEqual(answers, [200, 409])
        refused = next(result["value"] for result in (one, other) if result["value"].status_code == 409)
        self.assertEqual(refused.json()["code"], "post_not_failed")
        post.refresh_from_db()
        self.assertEqual(post.status, Status.SCHEDULED)

    def test_a_stuck_post_that_a_late_attempt_takes_again_is_not_retried(self):
        post = self.make_post(Status.PROCESSING, publish_at=timezone.now() - timedelta(hours=1))
        ScheduledPost.objects.filter(pk=post.pk).update(
            updated_at=timezone.now() - service.POST_STUCK_AFTER - timedelta(minutes=5),
        )
        paused, taken, release = self.pause_in_save(Status.PROCESSING)
        sent, send = self.network()
        with paused, patch(PRESIGN, return_value="https://media.test/a.mp4"), send as send:
            # A retry of the worker that came late: it takes the post again before the user's retry.
            worker, _ = self.in_thread(lambda: upload_to_social_network.apply(args=[post.pk]).get())
            self.assertTrue(taken.wait(10), "the worker never took the post")
            retry, retried = self.in_thread(self.request, "post", reverse("scheduled-post-retry", args=[post.pk]))
            self.wait_until_someone_waits_for_a_lock()
            release.set()
            self.finish(retry)
            sent.set()
            self.finish(worker)

        response = retried["value"]
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))
        send.assert_called_once()  # published once, by the worker
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PUBLISHED)


class CrosspostingRacesTests(PostRacesHarness):
    """
    Review of #184: on a plan without crossposting, two requests that put one
    clip on two networks at the same time count the networks one after the
    other (the project and clip row locks), so one of them is a 403.
    """

    def setUp(self):
        super().setUp()
        Workspace.objects.filter(pk=self.home.pk).update(
            subscription_plan=SubscriptionPlan.objects.create(
                name="Creador de carrera", allow_scheduling=True, allow_crossposting=False, max_video_duration_seconds=3600,
            ),
        )

    def together(self, *calls):
        """Each request stops right before it counts the networks until the other gets there too, or for two seconds."""
        meeting = threading.Barrier(len(calls))
        count = service._check_crossposting

        def meet_then_count(*args, **kwargs):
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return count(*args, **kwargs)

        with patch.object(service, "_check_crossposting", meet_then_count):
            runs = [self.in_thread(self.request, *call) for call in calls]
            self.finish(*(thread for thread, _ in runs))
        return [result["value"] for _, result in runs]

    def assert_one_each(self, answers, ok):
        self.assertEqual(sorted(answer.status_code for answer in answers), sorted([ok, 403]))
        refused = next(answer for answer in answers if answer.status_code == 403)
        self.assertEqual(refused.json()["code"], "crossposting")
        live = ScheduledPost.objects.filter(video_clip=self.clip).exclude(status=Status.DRAFT)
        self.assertEqual(live.values("platform").distinct().count(), 1)

    def test_two_creates_of_one_clip_on_two_networks(self):
        url = reverse("scheduled-post-list")
        answers = self.together(
            ("post", url, {"video_clip": str(self.clip.pk), "platform": "INSTAGRAM_REELS"}),
            ("post", url, {"video_clip": str(self.clip.pk), "platform": "YOUTUBE_SHORTS"}),
        )
        self.assert_one_each(answers, 201)

    def test_two_drafts_of_one_clip_sent_at_once(self):
        drafts = [
            ScheduledPost.objects.create(video_clip=self.clip, platform=platform, status=Status.DRAFT, publish_at=timezone.now())
            for platform in ("INSTAGRAM_REELS", "YOUTUBE_SHORTS")
        ]
        answers = self.together(*[
            ("patch", reverse("scheduled-post-detail", args=[draft.pk]), {"publish_at": None}) for draft in drafts
        ])
        self.assert_one_each(answers, 200)

    def test_a_create_and_a_draft_sent_at_once(self):
        draft = ScheduledPost.objects.create(
            video_clip=self.clip, platform="YOUTUBE_SHORTS", status=Status.DRAFT, publish_at=timezone.now(),
        )
        answers = self.together(
            ("post", reverse("scheduled-post-list"), {"video_clip": str(self.clip.pk), "platform": "INSTAGRAM_REELS"}),
            ("patch", reverse("scheduled-post-detail", args=[draft.pk]), {"publish_at": None}),
        )
        self.assertEqual(sorted(answer.status_code for answer in answers), [201, 403] if answers[0].status_code == 201 else [200, 403])
        self.assertEqual(
            ScheduledPost.objects.filter(video_clip=self.clip).exclude(status=Status.DRAFT).values("platform").distinct().count(), 1,
        )


class AiBudgetRacesTests(PostRacesHarness):
    """
    Review of #184: the AI text budget is counted under an advisory lock of the
    workspace (services/scheduled_posts.lock_ai_budget), not under its row:
    two creates count it one after the other, and a create never deadlocks
    with what locks a project and then its workspace (a paid render, a
    re-dispatch) or with a workspace delete (projects, then the workspace).
    """

    def setUp(self):
        super().setUp()
        Workspace.objects.filter(pk=self.home.pk).update(
            subscription_plan=SubscriptionPlan.objects.create(
                name="Pro con SEO de carrera", allow_scheduling=True, allow_crossposting=True,
                has_seo_optimization=True, max_video_duration_seconds=3600,
            ),
        )
        self.seo_clips = []
        for title in ("Uno", "Dos"):
            project = VideoProject.objects.create(
                workspace=self.home, uploaded_by=self.alice, title=title, source_file="videos/raw/a.mp4",
                metadata={"use_seo": True},
            )
            self.seo_clips.append(VideoClip.objects.create(
                project=project, title=f"Clip {title}", start_time=0, end_time=30,
                status=VideoClip.Status.COMPLETED, s3_object_key="clips/a.mp4",
            ))

    def draft(self, clip):
        return ("post", reverse("scheduled-post-list"), {"video_clip": str(clip.pk), "platform": "TIKTOK", "draft": True})

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=1)
    def test_two_creates_count_the_budget_one_after_the_other(self):
        meeting = threading.Barrier(2)
        count = service.seo_budget

        def meet_then_count(*args, **kwargs):
            # With the budget's lock the second create never gets here while the first waits.
            try:
                meeting.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return count(*args, **kwargs)

        # Two clips of two projects: their own row locks do not order the creates.
        with patch.object(service, "seo_budget", meet_then_count), patch(SEO_DELAY) as delay:
            runs = [self.in_thread(self.request, *self.draft(clip)) for clip in self.seo_clips]
            self.finish(*(thread for thread, _ in runs))

        answers = [result["value"] for _, result in runs]
        self.assertEqual([answer.status_code for answer in answers], [201, 201])
        self.assertEqual(sorted(answer.json()["seo_status"] for answer in answers), ["failed", "pending"])
        self.assertEqual(delay.call_count, 1)

    def test_a_create_and_a_redispatch_of_its_project_never_deadlock(self):
        clip = self.seo_clips[0]
        locked, go = threading.Event(), threading.Event()
        take = service.lock_ai_budget

        def take_then_wait(*args, **kwargs):
            take(*args, **kwargs)
            # The create holds the budget's lock and has not locked the project yet.
            locked.set()
            go.wait(15)

        with patch.object(service, "lock_ai_budget", take_then_wait), patch(SEO_DELAY):
            create, created = self.in_thread(self.request, *self.draft(clip))
            self.assertTrue(locked.wait(10), "the create never took the budget's lock")
            # The re-dispatch locks the project and then its workspace (views_clips.ProjectRedispatchView).
            redispatch, redispatched = self.in_thread(
                self.request, "post", reverse("project-redispatch", args=[clip.project_id]),
            )
            redispatch.join(timeout=5)
            go.set()
            self.finish(create, redispatch)

        self.assertNotIn("error", created)
        self.assertNotIn("error", redispatched)
        self.assertEqual(created["value"].status_code, 201)
        # The project is not failed: the re-dispatch answers its own 409, after its locks.
        self.assertEqual((redispatched["value"].status_code, redispatched["value"].json()["code"]), (409, "not_failed"))
