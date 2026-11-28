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

from apps.integrations.tests.helpers import PUBLISH as SEND, PUBLISHED, give_social_profile
from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services import scheduled_posts as service
from apps.videos.tasks import dispatch_scheduled_posts_batch, upload_to_social_network

User = get_user_model()
Status = ScheduledPost.Status

PRESIGN = "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url"
UPLOAD = "apps.videos.tasks.upload_to_social_network.apply_async"
SEO_DELAY = "apps.videos.tasks.process_video_seo.delay"
SEO_APPLY_ASYNC = "apps.videos.tasks.process_video_seo.apply_async"
DISPATCH_DELAY = "apps.videos.tasks.dispatch_scheduled_posts_batch.delay"


class PostRacesHarness(APITransactionTestCase):
    # A write that leaves a post due now asks for a run of the dispatcher after its commit
    # (scheduled_posts.dispatch_if_due). Here the commits are real and Celery runs eagerly, so that run
    # would happen inside the request and send the post. Only DispatchAfterCommitRacesTests lets it run.
    dispatch_after_commit = False

    def setUp(self):
        if not self.dispatch_after_commit:
            dispatch = patch(DISPATCH_DELAY)
            dispatch.start()
            self.addCleanup(dispatch.stop)
        self.alice = User.objects.create_user(username="alice", email="alice@test.com", password="pw")
        self.home = self.alice.owned_workspaces.get()
        Workspace.objects.filter(pk=self.home.pk).update(
            # Its own plan: a TransactionTestCase empties the tables, the seeded plans too.
            subscription_plan=SubscriptionPlan.objects.create(
                name="Pro de carrera", allow_scheduling=True, allow_crossposting=True, max_video_duration_seconds=3600,
            ),
        )
        give_social_profile(self.home)
        # Deleting the workspace then deletes its profile at the provider, after the commit: no network here.
        unlink = patch("apps.integrations.social.ayrshare.AyrshareProvider.delete_profile")
        unlink.start()
        self.addCleanup(unlink.stop)
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

    def wait_until_someone_waits_for_a_lock(self, timeout=10, count=1, on=None):
        """
        Until ``count`` connections of this test database wait for a lock; with ``on``, only those
        whose statement selects from that table (pg_stat_activity cuts a long query, FOR UPDATE included).
        """
        deadline = time.monotonic() + timeout
        sql = "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        params = []
        if on:
            sql += " AND query LIKE %s"
            params = [f'SELECT "{on}".%']
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute(sql, params)
                if cursor.fetchone()[0] >= count:
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

        def answer(*args, **kwargs):
            sent.wait(15)
            return PUBLISHED

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
        self.assertEqual(worked["value"], "Published via Ayrshare: INSTAGRAM_REELS")
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


class DispatchAfterCommitRacesTests(PostRacesHarness):
    """
    Ensayo 5: a write that leaves a post due now runs the dispatcher once after its commit
    (scheduled_posts.dispatch_if_due), here for real (eager Celery, real commits). Two such runs and
    the beat's at the same time queue each post once: the dispatcher locks each post and checks its
    status and window again.
    """

    dispatch_after_commit = True

    def setUp(self):
        super().setUp()
        project = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title="Otra", source_file="videos/raw/b.mp4",
        )
        self.other_clip = VideoClip.objects.create(
            project=project, title="Otro clip", start_time=0, end_time=30, status=VideoClip.Status.COMPLETED,
            s3_object_key="clips/b.mp4",
        )

    def test_publish_now_is_queued_right_after_the_commit(self):
        queued = []
        with patch(UPLOAD, side_effect=lambda *args, **kwargs: queued.append(kwargs)):
            create, created = self.in_thread(self.request, "post", reverse("scheduled-post-list"), {
                "video_clip": str(self.clip.pk), "platform": "INSTAGRAM_REELS", "caption": "Ya",
            })
            self.finish(create)

        response = created["value"]
        self.assertEqual(response.status_code, 201, response.content)
        post = ScheduledPost.objects.get(pk=response.json()["id"])
        self.assertEqual(post.status, Status.QUEUED)  # without waiting for the beat
        self.assertEqual([(call["args"], call["eta"]) for call in queued], [([post.pk], post.publish_at)])

    def test_two_requests_and_the_beat_at_once_queue_each_post_once(self):
        long_ago = timezone.now() - timedelta(hours=1)
        retried = [
            ScheduledPost.objects.create(
                video_clip=self.clip, platform=platform, status=Status.FAILED, publish_at=long_ago,
                error_log="provider_unavailable",
            )
            for platform in ("INSTAGRAM_REELS", "YOUTUBE_SHORTS")
        ]
        # Due before the retried ones: every run of the dispatcher tries it first.
        due = ScheduledPost.objects.create(
            video_clip=self.other_clip, platform="INSTAGRAM_REELS", status=Status.SCHEDULED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        queued, taken, release = [], threading.Event(), threading.Event()
        record = threading.Lock()

        def queue(*args, **kwargs):
            # Inside a dispatcher's transaction, with the post's row lock held. The first one waits.
            with record:
                queued.append(kwargs["args"][0])
            if not taken.is_set():
                taken.set()
                release.wait(15)

        with patch(UPLOAD, side_effect=queue):
            beat, beaten = self.in_thread(dispatch_scheduled_posts_batch)
            self.assertTrue(taken.wait(10), "the beat never queued the post")
            runs = [
                self.in_thread(self.request, "post", reverse("scheduled-post-retry", args=[post.pk]))
                for post in retried
            ]
            # Each retry committed and its run of the dispatcher waits for the post the beat holds.
            self.wait_until_someone_waits_for_a_lock(count=2, on=ScheduledPost._meta.db_table)
            release.set()
            self.finish(beat, *(thread for thread, _ in runs))

            self.assertEqual(dispatch_scheduled_posts_batch(), "Queued 0 posts.")  # the next beat: nothing left

        self.assertNotIn("error", beaten)
        self.assertEqual(beaten["value"], "Queued 1 posts.")
        self.assertEqual([result["value"].status_code for _, result in runs], [200, 200])
        self.assertEqual(sorted(queued), sorted([due.pk, *(post.pk for post in retried)]))
        self.assertEqual(
            set(ScheduledPost.objects.filter(pk__in=[due.pk, *(post.pk for post in retried)]).values_list("status", flat=True)),
            {Status.QUEUED},
        )


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
        """
        Since #181 the re-dispatch locks only its project (`of=('self',)`), so this test no
        longer catches, on its own, a create that locks the workspace's row: the delete test
        below does. It stays because it runs the real re-dispatch next to a create of the same
        project, and it deadlocks again if both go back to locking the workspace's row, the
        create first and the re-dispatch after its project (as before #181).
        """
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

    def test_a_create_and_a_delete_of_its_workspace_never_deadlock(self):
        """The delete locks the projects and then the workspace's row (#181); the create, the budget and then a project."""
        Workspace.objects.create(name="Otro", owner=self.alice)  # not the owner's last one
        # Finished projects: the delete refuses a workspace with something processing.
        VideoProject.objects.filter(workspace=self.home).update(status=VideoProject.Status.COMPLETED)
        clip = self.seo_clips[0]
        locked, go = threading.Event(), threading.Event()
        take = service.lock_ai_budget

        def take_then_wait(*args, **kwargs):
            take(*args, **kwargs)
            locked.set()
            go.wait(15)

        with patch.object(service, "lock_ai_budget", take_then_wait), patch(SEO_DELAY):
            create, created = self.in_thread(self.request, *self.draft(clip))
            self.assertTrue(locked.wait(10), "the create never took the budget's lock")
            delete, deleted = self.in_thread(
                self.request, "delete", reverse("workspace-detail", args=[self.home.pk]), {"confirm_name": self.home.name},
            )
            delete.join(timeout=5)
            go.set()
            self.finish(create, delete)

        self.assertNotIn("error", created)
        self.assertNotIn("error", deleted)
        self.assertEqual(deleted["value"].status_code, 204)
        # The create went on after the delete: its clip is gone with the workspace.
        self.assertEqual((created["value"].status_code, created["value"].json()), (400, {"video_clip": ["El clip no existe."]}))
        self.assertFalse(Workspace.objects.filter(pk=self.home.pk).exists())



class AiBudgetLockScopeTests(PostRacesHarness):
    """
    Review of #184: the budget's advisory lock ends with its transaction
    (pg_advisory_xact_lock), after a request that created posts and after one
    that was refused. A session lock (pg_advisory_lock) would stay with the
    connection of the request, and every later create of the workspace would wait.
    """

    def setUp(self):
        super().setUp()
        VideoProject.objects.filter(pk=self.clip.project_id).update(metadata={"use_seo": True})

    def free_from_another_connection(self):
        free = {}

        def run():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, %s)",
                        [service.AI_BUDGET_LOCK_NAMESPACE, service._advisory_key(self.home.pk)],
                    )
                    free["value"] = cursor.fetchone()[0]
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        return free.get("value")

    def test_the_lock_ends_with_the_request(self):
        api = APIClient()
        api.force_authenticate(self.alice)
        ws = {"HTTP_X_WORKSPACE_ID": str(self.home.pk)}
        url = reverse("scheduled-post-list")
        with patch(SEO_DELAY):
            created = api.post(url, {"video_clip": str(self.clip.pk), "platform": "TIKTOK", "draft": True}, format="json", **ws)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIs(self.free_from_another_connection(), True)

        # Refused inside the transaction, after the lock: a date in the past.
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        refused = api.post(url, {"video_clip": str(self.clip.pk), "platform": "YOUTUBE_SHORTS", "publish_at": past}, format="json", **ws)
        self.assertEqual(refused.status_code, 400)
        self.assertIs(self.free_from_another_connection(), True)

        # "Regenerar" takes it too: refused after it, the post is not on a plan with SEO here.
        regenerate = api.post(reverse("scheduled-post-regenerate-seo", args=[created.json()["id"]]), format="json", **ws)
        self.assertEqual(regenerate.status_code, 403)
        self.assertIs(self.free_from_another_connection(), True)


class TextEditRacesTests(PostRacesHarness):
    """
    Review of #184: a text the user saves while a "Regenerar" holds the post is
    newer than that request, so the AI's answer keeps it. Both timestamps come
    from the database's clock in statements written under the post's row lock.
    """

    def setUp(self):
        super().setUp()
        Workspace.objects.filter(pk=self.home.pk).update(
            subscription_plan=SubscriptionPlan.objects.create(
                name="Pro con SEO de edición", allow_scheduling=True, allow_crossposting=True,
                has_seo_optimization=True, max_video_duration_seconds=3600,
            ),
        )
        VideoProject.objects.filter(pk=self.clip.project_id).update(
            metadata={"use_seo": True}, transcript_data=[{"start": 1, "end": 2, "text": "hola"}],
        )

    def test_a_text_saved_while_a_regeneration_holds_the_post_stays(self):
        from apps.videos import signals
        from apps.videos.tasks import process_video_seo
        from apps.videos.tests.fakes import FakeRedis
        from apps.videos.tests.test_seo_task import CAPTION, generated

        post = ScheduledPost.objects.create(
            video_clip=self.clip, platform=ScheduledPost.Platform.INSTAGRAM_REELS, status=Status.DRAFT,
            publish_at=timezone.now(), generated_caption="Viejo", generated_hashtags=["viejo"],
        )
        held, go = threading.Event(), threading.Event()
        queue = signals.queue_post_seo

        def hold_then_queue(*args, **kwargs):
            # "Regenerar" holds the post's row and has not marked its request yet.
            held.set()
            go.wait(15)
            return queue(*args, **kwargs)

        with patch.object(signals, "queue_post_seo", hold_then_queue), patch(SEO_APPLY_ASYNC):
            regenerate, regenerated = self.in_thread(
                self.request, "post", reverse("scheduled-post-regenerate-seo", args=[post.pk]),
            )
            self.assertTrue(held.wait(10), "the regeneration never held the post")
            edit, edited = self.in_thread(
                self.request, "patch", reverse("scheduled-post-detail", args=[post.pk]),
                {"caption": "Lo escribí yo", "hashtags": ["mio"]},
            )
            self.wait_until_someone_waits_for_a_lock()
            go.set()
            self.finish(regenerate, edit)

        self.assertEqual((regenerated["value"].status_code, edited["value"].status_code), (202, 200))
        post.refresh_from_db()
        self.assertGreater(post.text_edited_at, post.seo_requested_at)

        with patch("apps.core.security.AI_Security_Shield.check_content_safety"), \
                patch("apps.videos.services.ai.project_lock.get_redis_client", return_value=FakeRedis()), \
                patch("apps.videos.services.seo_engine.SEOOptimizationService") as seo:
            seo.return_value.generate_metadata.return_value = generated()
            process_video_seo.push_request(retries=0)
            try:
                self.assertEqual(process_video_seo.run(str(post.pk), regenerate=True), "SEO Success")
            finally:
                process_video_seo.pop_request()

        post.refresh_from_db()
        self.assertEqual((post.caption, post.hashtags, post.generated_caption), ("Lo escribí yo", ["mio"], CAPTION))
