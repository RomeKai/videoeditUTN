"""
upload_to_social_network through the provider layer (apps.integrations.social):
what it sends (the API's text, TikTok's options, YouTube's title), what it
stores (post_url, external_id, a code in error_log that the API translates),
and the idempotency key: the worker's retries never publish twice, and the
user's "Reintentar" can publish again.
"""
import threading
from unittest.mock import MagicMock, patch

from http.client import RemoteDisconnected

import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient
from urllib3.exceptions import ProtocolError

from apps.integrations.models import SocialProfile
from apps.integrations.social import (
    PUBLISH_LOCK_TTL_SECONDS,
    OutcomeUnknownError,
    PermanentError,
    PublishResult,
    RetryableError,
    TikTokOptions,
    idempotency_key,
)
from apps.integrations.tests.helpers import PUBLISH, PUBLISHED, give_social_profile, take_social_profile
from apps.users.views_workspaces import POST_PROCESSING_WINDOW
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.serializers_social import ScheduledPostSerializer
from apps.videos.services.seo_engine import compose_caption
from apps.videos.tasks import dispatch_scheduled_posts_batch, upload_to_social_network
from apps.videos.tests.fakes import CapturedTaskFailures

User = get_user_model()
Status, Platform = ScheduledPost.Status, ScheduledPost.Platform
PRESIGN = "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url"
REQUEST = "apps.integrations.social.ayrshare.requests.request"
TIKTOK = {"privacy_level": "FOLLOWER_OF_CREATOR", "allow_comment": True, "allow_duet": False,
          "allow_stitch": True, "brand_organic": False, "branded_content": True}


class PublisherHarness(CapturedTaskFailures):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="pub", email="pub@test.com", password="pw")
        self.workspace = self.user.owned_workspaces.get()
        give_social_profile(self.workspace, "PROFILE-KEY")
        self.project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.user, title="Entrevista")
        self.clip = VideoClip.objects.create(
            project=self.project, title="La remontada", start_time=0, end_time=30,
            status=VideoClip.Status.COMPLETED, s3_object_key="clips/a.mp4",
        )
        presign = patch(PRESIGN, return_value="https://r2.test/clips/a.mp4?sig=1")
        presign.start()
        self.addCleanup(presign.stop)

    def make_post(self, platform=Platform.INSTAGRAM_REELS, status=Status.QUEUED, **fields):
        if platform == Platform.TIKTOK:
            fields.setdefault("tiktok", TIKTOK)
        return ScheduledPost.objects.create(
            video_clip=self.clip, platform=platform, status=status, publish_at="2030-01-01T00:00:00Z", **fields,
        )

    def run_task(self, post):
        result = upload_to_social_network.apply(args=[str(post.pk)])
        post.refresh_from_db()
        return result

    def sent(self, publish):
        """The PublishRequest of each call to the provider."""
        return [call.args[1] for call in publish.call_args_list]


class WhatGoesOutTests(PublisherHarness, TestCase):
    def test_the_users_text_and_hashtags_go_out(self):
        post = self.make_post(caption="Mirá esto", hashtags=["futbol", "gol"],
                              generated_caption="Texto de la IA", generated_hashtags=["ia"])
        with patch(PUBLISH, return_value=PUBLISHED) as publish:
            self.run_task(post)
        self.assertEqual(self.sent(publish)[0].text, "Mirá esto\n\n#futbol #gol")

    def test_each_part_falls_back_to_the_ai_on_its_own(self):
        """The same rule as the API's `caption` and `hashtags` (ScheduledPostSerializer)."""
        cases = (
            ({"caption": "Mío", "generated_caption": "IA", "generated_hashtags": ["ia"]}, "Mío\n\n#ia"),
            ({"hashtags": ["mio"], "generated_caption": "IA", "generated_hashtags": ["ia"]}, "IA\n\n#mio"),
            ({"generated_caption": "IA", "generated_hashtags": ["ia", "dos"]}, "IA\n\n#ia #dos"),
        )
        for fields, text in cases:
            with self.subTest(fields=fields), patch(PUBLISH, return_value=PUBLISHED) as publish:
                post = self.make_post(**fields)
                shown = ScheduledPostSerializer(post).data
                self.run_task(post)
                self.assertEqual(self.sent(publish)[0].text, text)
                self.assertEqual(text, compose_caption(shown["caption"], shown["hashtags"]))

    def test_without_any_text_nothing_is_made_up(self):
        """No English placeholder ("Check this out! #Viral"): an empty text, which the networks accept."""
        with patch(PUBLISH, return_value=PUBLISHED) as publish:
            self.run_task(self.make_post())
        self.assertEqual(self.sent(publish)[0].text, "")

    def test_tiktok_options_and_youtube_title_go_out(self):
        with patch(PUBLISH, return_value=PUBLISHED) as publish:
            self.run_task(self.make_post(Platform.TIKTOK))
            self.run_task(self.make_post(Platform.YOUTUBE_SHORTS))
        tiktok, youtube = self.sent(publish)
        self.assertEqual(tiktok.tiktok, TikTokOptions(
            "FOLLOWER_OF_CREATOR", allow_comment=True, allow_duet=False, allow_stitch=True,
            brand_organic=False, branded_content=True,
        ))
        self.assertEqual(youtube.title, "La remontada")
        self.assertIsNone(youtube.tiktok)

    def test_the_ayrshare_body_carries_the_tiktok_options(self):
        """End to end: the post's options as Ayrshare's tikTokOptions."""
        answer = MagicMock(status_code=200, headers={})
        answer.json.return_value = {"status": "success", "id": "ayr-9", "errors": [], "postIds": [
            {"status": "success", "platform": "tiktok", "id": "pending", "idShare": "v_pub_url~1"},
        ]}
        post = self.make_post(Platform.TIKTOK, caption="Hola", hashtags=["a"])
        with patch(REQUEST, return_value=answer) as sent:
            self.run_task(post)
        body = sent.call_args.kwargs["json"]
        self.assertEqual(body["tikTokOptions"], {
            "visibility": "followers", "disableComments": False, "disableDuet": True, "disableStitch": False,
            "isBrandOrganic": False, "isBrandedContent": True,
        })
        self.assertEqual((body["post"], body["isVideo"]), ("Hola\n\n#a", True))
        self.assertEqual(sent.call_args.kwargs["headers"]["Profile-Key"], "PROFILE-KEY")
        # TikTok still processes it: published, with the provider's id and no link yet.
        self.assertEqual((post.status, post.post_url, post.external_id), (Status.PUBLISHED, None, "ayr-9"))

    def test_a_tiktok_post_without_options_is_not_published(self):
        post = self.make_post(Platform.TIKTOK, tiktok=None)
        with patch(PUBLISH) as publish:
            result = self.run_task(post)
        publish.assert_not_called()
        self.assertTrue(result.failed())
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "tiktok_options_missing"))
        row = ScheduledPostSerializer(post).data
        self.assertEqual(row["error_code"], "tiktok_options_missing")
        self.assertIn("TikTok", row["error_message"])


class OutcomeTests(PublisherHarness, TestCase):
    def test_the_link_and_the_id_are_kept(self):
        post = self.make_post(error_log="Retry 1: provider_unavailable (ayrshare http=503)")
        with patch(PUBLISH, return_value=PublishResult("ayr-1", "https://www.instagram.com/reel/abc/")):
            result = self.run_task(post)
        self.assertEqual(result.result, "Published via Ayrshare: INSTAGRAM_REELS")
        self.assertEqual(
            (post.status, post.post_url, post.external_id, post.error_log),
            (Status.PUBLISHED, "https://www.instagram.com/reel/abc/", "ayr-1", None),
        )

    def test_permanent_errors_fail_the_post_with_their_code_at_once(self):
        for code in ("account_not_connected", "account_expired", "quota_exceeded", "duplicate_content",
                     "provider_rejected"):
            with self.subTest(code=code), patch(PUBLISH, side_effect=PermanentError(code, "ayrshare http=400 code=1")) as publish:
                post = self.make_post()
                result = self.run_task(post)
                publish.assert_called_once()  # never retried
                self.assertTrue(result.failed())
                self.assertEqual((post.status, post.error_log), (Status.FAILED, f"{code} (ayrshare http=400 code=1)"))
                row = ScheduledPostSerializer(post).data
                self.assertEqual(row["error_code"], code)
                self.assertNotIn("No pudimos publicar", row["error_message"])

    def test_an_unknown_outcome_is_never_retried(self):
        post = self.make_post()
        with patch(PUBLISH, side_effect=OutcomeUnknownError(detail="ayrshare http=504")) as publish:
            result = self.run_task(post)
        publish.assert_called_once()
        self.assertTrue(result.failed())
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "outcome_unknown (ayrshare http=504)"))
        self.assertIn("No sabemos si se publicó", ScheduledPostSerializer(post).data["error_message"])

    def test_a_refusal_after_an_unclear_attempt_may_be_the_repeated_key(self):
        """The first attempt may have published (a 5xx that said nothing): a refusal of the same key is unknown."""
        post = self.make_post()
        with patch(PUBLISH, side_effect=[RetryableError(detail="ayrshare http=502", maybe_sent=True),
                                         PermanentError("provider_rejected", "ayrshare http=400 code=999")]):
            self.run_task(post)
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "outcome_unknown (ayrshare http=400 code=999)"))

    def test_a_refusal_after_attempts_that_sent_nothing_is_a_refusal(self):
        """Review of #186: retries where nothing reached the provider (the lock, no connection) prove nothing."""
        post = self.make_post()
        with patch(PUBLISH, side_effect=[RetryableError(detail="ayrshare ConnectTimeout"),
                                         RetryableError(detail="ayrshare http=429"),
                                         PermanentError("provider_rejected", "ayrshare http=400 code=999")]):
            self.run_task(post)
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "provider_rejected (ayrshare http=400 code=999)"))
        self.assertFalse(post.publish_key_unclear)

    def test_an_unexpected_error_in_the_call_may_have_published(self):
        """A bug after the request went out is not «try again»."""
        post = self.make_post()
        with patch(PUBLISH, side_effect=KeyError("postIds")):
            self.run_task(post)
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "outcome_unknown (critical KeyError)"))
        self.assertEqual(ScheduledPostSerializer(post).data["error_code"], "outcome_unknown")

    def test_a_post_that_moved_on_is_not_published_by_a_late_message(self):
        for status in (Status.DRAFT, Status.SCHEDULED, Status.FAILED):
            with self.subTest(status=status), patch(PUBLISH) as publish:
                post = self.make_post(status=status, error_log="x")
                result = self.run_task(post)
                publish.assert_not_called()
                self.assertEqual(result.result, "Skipped")
                self.assertEqual((post.status, post.error_log), (status, "x"))

    def test_the_provider_is_the_one_of_the_profile(self):
        SocialProfile.objects.filter(workspace=self.workspace).update(provider="otro")
        post = self.make_post()
        with patch(PUBLISH) as publish:
            self.run_task(post)
        publish.assert_not_called()
        self.assertEqual(post.error_log, "provider_not_configured (unknown social provider 'otro')")
        # Ours to fix: the user sees the generic message.
        self.assertEqual(ScheduledPostSerializer(post).data["error_code"], "publish_failed")

    def test_a_provider_wait_is_honoured_up_to_the_longest_backoff(self):
        for retry_after, countdown in ((None, 60), (200, 200), (5000, 240)):
            with self.subTest(retry_after=retry_after), \
                    patch(PUBLISH, side_effect=[RetryableError(retry_after=retry_after), PUBLISHED]), \
                    patch.object(upload_to_social_network, "retry", wraps=upload_to_social_network.retry) as retry:
                self.run_task(self.make_post())
                self.assertEqual(retry.call_args.kwargs["countdown"], countdown)

    def test_the_lock_lives_as_long_as_before(self):
        """The window of a PROCESSING post (API, workspace delete) moved to the provider layer unchanged."""
        self.assertEqual(PUBLISH_LOCK_TTL_SECONDS, 600)
        self.assertEqual(POST_PROCESSING_WINDOW.total_seconds(), 600)
        locks = MagicMock()
        locks.add.return_value = False
        with patch("apps.videos.tasks.social.caches", {"locks": locks}):
            self.assertEqual(upload_to_social_network.apply(args=[str(self.make_post().pk)]).get(), "Locked")
        self.assertEqual(locks.add.call_args.args[2], 600)


class LegacyErrorTests(PublisherHarness, TestCase):
    def test_what_failed_posts_of_before_say(self):
        cases = {
            "Error: Ayrshare Profile Key not configured in workspace.": "account_not_connected",
            "Ayrshare did not answer in time; the post may have been published. Check the network.": "outcome_unknown",
            "Failed after 4 attempts: Ayrshare error: rate limited": "publish_failed",
            "Critical: r2 down": "publish_failed",
        }
        for log, code in cases.items():
            with self.subTest(log=log):
                row = ScheduledPostSerializer(self.make_post(status=Status.FAILED, error_log=log)).data
                self.assertEqual(row["error_code"], code)

    def test_messages_name_the_network(self):
        post = self.make_post(Platform.YOUTUBE_SHORTS, status=Status.FAILED, error_log="account_expired (ayrshare http=400 code=161)")
        self.assertEqual(ScheduledPostSerializer(post).data["error_message"],
                         "La cuenta de YouTube Shorts venció. Reconectala y reintentá.")


class IdempotencyKeyTests(PublisherHarness, TestCase):
    def keys(self, publish):
        return [request.idempotency_key for request in self.sent(publish)]

    def test_the_workers_retries_send_the_same_key(self):
        post = self.make_post()
        with patch(PUBLISH, side_effect=[RetryableError(), RetryableError(), PUBLISHED]) as publish:
            self.run_task(post)
        self.assertEqual(self.keys(publish), [idempotency_key(post.pk, 0)] * 3)
        self.assertEqual((post.status, post.publish_key_version), (Status.PUBLISHED, 0))

    def test_a_definitive_refusal_spends_the_key(self):
        """The provider answered that nothing went out: the next attempt needs a key it never saw."""
        post = self.make_post()
        with patch(PUBLISH, side_effect=[RetryableError(key_spent=True), PUBLISHED]) as publish:
            self.run_task(post)
        self.assertEqual(self.keys(publish), [idempotency_key(post.pk, 0), idempotency_key(post.pk, 1)])

    def test_reintentar_gets_a_new_key(self):
        post = self.make_post()
        with patch(PUBLISH, side_effect=OutcomeUnknownError(detail="ayrshare ReadTimeout")) as publish:
            self.run_task(post)
        api = APIClient()
        api.force_authenticate(self.user)
        self.assertTrue(post.publish_key_unclear)
        response = api.post(reverse("scheduled-post-retry", args=[post.pk]), HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        self.assertEqual(response.status_code, 200, response.content)
        post.refresh_from_db()
        # A key the provider never saw: nothing is unclear about it.
        self.assertEqual((post.status, post.publish_key_version, post.retry_count, post.publish_key_unclear),
                         (Status.SCHEDULED, 1, 0, False))

        with patch(PUBLISH, return_value=PUBLISHED) as again:
            dispatch_scheduled_posts_batch()  # eager in tests: the task runs now
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PUBLISHED)
        self.assertEqual(self.keys(publish) + self.keys(again), [idempotency_key(post.pk, 0), idempotency_key(post.pk, 1)])

    def test_a_retry_of_an_ai_text_failure_keeps_the_key(self):
        post = self.make_post(status=Status.FAILED, error_log="seo_no_clip_transcript")
        api = APIClient()
        api.force_authenticate(self.user)
        api.post(reverse("scheduled-post-retry", args=[post.pk]), HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        post.refresh_from_db()
        self.assertEqual((post.status, post.publish_key_version), (Status.DRAFT, 0))


def ayrshare_answer(status, body=None):
    response = MagicMock(headers={}, status_code=status)
    if body is None:
        response.json.side_effect = ValueError("an HTML page of the CDN")
    else:
        response.json.return_value = body
    return response


RATE_LIMITED = (429, {"status": "error", "code": 0, "message": "Too many requests"})
TIKTOK_DOWN = (503, {"status": "error", "errors": [{"code": 531, "retryAvailable": True, "platform": "instagram"}]})
BAD_GATEWAY = (502, None)


class FakeAyrshare:
    """
    Ayrshare's /post as its documentation says: a key it already saw is refused
    whatever happened to the first post. ``drop_answer`` loses the answer of the
    next post it publishes: ``"reset"`` cuts the connection after it published,
    ``"bad_gateway"`` answers a 502 page that says nothing. ``busy`` answers the
    calls after that one with these (status, body) before looking at the key
    (the rate limit, an outage).
    """

    def __init__(self, drop_answer=None, busy=()):
        self.keys, self.published, self.drop_answer, self.busy = set(), [], drop_answer, list(busy)

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        key = json["idempotencyKey"]
        if self.published and self.busy:
            return ayrshare_answer(*self.busy.pop(0))
        response = MagicMock(headers={})
        if key in self.keys:
            response.status_code = 400
            response.json.return_value = {"status": "error", "postIds": [], "errors": [
                {"action": "post", "status": "error", "code": 999, "platform": "instagram",
                 "message": "Duplicate idempotencyKey. A post with this idempotency key already exists."},
            ]}
            return response
        self.keys.add(key)
        self.published.append(key)
        lost, self.drop_answer = self.drop_answer, None
        if lost == "reset":
            raise requests.exceptions.ConnectionError(
                ProtocolError("Connection aborted.", RemoteDisconnected("Remote end closed connection")),
            )
        if lost == "bad_gateway":
            return ayrshare_answer(*BAD_GATEWAY)
        response.status_code = 200
        response.json.return_value = {"status": "success", "id": f"ayr-{len(self.published)}", "errors": [], "postIds": [
            {"status": "success", "platform": "instagram", "id": "1787", "postUrl": "https://www.instagram.com/reel/x/"},
        ]}
        return response


class FakeAyrshareTests(PublisherHarness, TestCase):
    def publish_against(self, ayrshare):
        post = self.make_post()
        with patch(REQUEST, side_effect=ayrshare):
            self.run_task(post)
        return post

    def test_a_lost_answer_is_never_published_twice_by_the_worker_but_reintentar_can(self):
        ayrshare = FakeAyrshare(drop_answer="bad_gateway")
        post = self.publish_against(ayrshare)
        # Published once; the worker's retry carried the same key and Ayrshare refused it.
        self.assertEqual(len(ayrshare.published), 1)
        self.assertEqual(post.status, Status.FAILED)
        self.assertEqual(post.error_log, "outcome_unknown (ayrshare http=400 code=999 idempotency)")
        self.assertIn("No sabemos si se publicó", ScheduledPostSerializer(post).data["error_message"])

        # The user checked the network and asks for it again: a new key, and Ayrshare takes it.
        api = APIClient()
        api.force_authenticate(self.user)
        api.post(reverse("scheduled-post-retry", args=[post.pk]), HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        with patch(REQUEST, side_effect=ayrshare):
            dispatch_scheduled_posts_batch()
        post.refresh_from_db()
        self.assertEqual(len(ayrshare.published), 2)
        self.assertEqual((post.status, post.external_id, post.publish_key_unclear), (Status.PUBLISHED, "ayr-2", False))

    def test_a_connection_cut_after_sending_is_never_sent_again(self):
        """Review of #186: Ayrshare may still be publishing it (it downloads the video inside the request)."""
        ayrshare = FakeAyrshare(drop_answer="reset")
        post = self.publish_against(ayrshare)
        self.assertEqual(len(ayrshare.published), 1)
        self.assertEqual(post.retry_count, 0)
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "outcome_unknown (ayrshare ConnectionError)"))

    def test_a_refusal_before_the_key_after_a_lost_answer_never_changes_the_key(self):
        """
        Review of #186: the first answer is lost after Ayrshare published; the next attempt is refused
        before Ayrshare looks at the key (rate limit, or TikTok down with retryAvailable). A new key there
        would publish a second copy: the key stays, Ayrshare refuses the third attempt as a repeat.
        """
        for name, busy in (("429", [RATE_LIMITED]), ("503 retryAvailable", [TIKTOK_DOWN])):
            with self.subTest(busy=name):
                ayrshare = FakeAyrshare(drop_answer="bad_gateway", busy=busy)
                post = self.publish_against(ayrshare)
                self.assertEqual(ayrshare.published, [idempotency_key(post.pk, 0)])
                self.assertEqual(post.publish_key_version, 0)
                self.assertEqual((post.status, post.error_log),
                                 (Status.FAILED, "outcome_unknown (ayrshare http=400 code=999 idempotency)"))

    def test_running_out_of_retries_after_a_lost_answer_is_an_unknown_outcome(self):
        """Review of #186: never «Probá de nuevo» for a post that may be out (and «Reintentar» would duplicate it)."""
        ayrshare = FakeAyrshare(drop_answer="bad_gateway", busy=[BAD_GATEWAY] * 3)
        post = self.publish_against(ayrshare)
        self.assertEqual(len(ayrshare.published), 1)
        self.assertEqual((post.status, post.error_log),
                         (Status.FAILED, "outcome_unknown (after 4 attempts; ayrshare http=502)"))
        row = ScheduledPostSerializer(post).data
        self.assertEqual(row["error_code"], "outcome_unknown")
        self.assertIn("No sabemos si se publicó", row["error_message"])

    def test_without_a_lost_answer_a_temporary_refusal_gets_a_new_key(self):
        """The other side: nothing unclear before, Ayrshare says nothing went out and to retry: a fresh key works."""
        ayrshare = FakeAyrshare()
        calls = []

        def down_once(*args, **kwargs):
            calls.append(1)
            return ayrshare_answer(*TIKTOK_DOWN) if len(calls) == 1 else ayrshare(*args, **kwargs)

        post = self.make_post()
        with patch(REQUEST, side_effect=down_once):
            self.run_task(post)
        self.assertEqual((post.status, post.publish_key_version, post.publish_key_unclear), (Status.PUBLISHED, 1, False))
        self.assertEqual(ayrshare.published, [idempotency_key(post.pk, 1)])

    def test_a_worker_that_dies_in_the_call_leaves_the_key_unclear(self):
        """The mark is set before the call: a worker killed inside it (no handler runs) leaves it set."""
        post = self.make_post()
        with patch(PUBLISH, side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            upload_to_social_network.apply(args=[str(post.pk)])
        post.refresh_from_db()
        self.assertEqual((post.status, post.publish_key_unclear), (Status.PROCESSING, True))

    def test_a_redelivery_after_a_dead_worker_is_an_unknown_outcome(self):
        """A redelivery after a crash inside the call: Ayrshare refusing the key is an unknown outcome."""
        ayrshare = FakeAyrshare()
        post = self.make_post(status=Status.PROCESSING)
        ayrshare.keys.add(idempotency_key(post.pk, 0))  # the dead worker's request reached Ayrshare
        ScheduledPost.objects.filter(pk=post.pk).update(publish_key_unclear=True)
        with patch(REQUEST, side_effect=ayrshare):
            self.run_task(post)
        self.assertEqual((post.status, post.error_log),
                         (Status.FAILED, "outcome_unknown (ayrshare http=400 code=999 idempotency)"))


class PublishLockRaceTests(PublisherHarness, TransactionTestCase):
    """Two deliveries of the same post at once (a redelivery, a double queue): the real lock lets one through."""

    def test_two_deliveries_at_once_publish_once(self):
        from django.core.cache import caches

        caches["locks"].clear()
        self.addCleanup(caches["locks"].clear)
        post = self.make_post()
        inside, release = threading.Event(), threading.Event()

        def slow_publish(*args, **kwargs):
            inside.set()
            release.wait(15)
            return PUBLISHED

        results = {}

        def deliver(name):
            try:
                results[name] = upload_to_social_network.apply(args=[str(post.pk)]).result
            finally:
                connection.close()

        with patch(PUBLISH, side_effect=slow_publish) as publish:
            first = threading.Thread(target=deliver, args=("first",))
            first.start()
            self.assertTrue(inside.wait(10), "the first delivery never reached the provider")
            second = threading.Thread(target=deliver, args=("second",))
            second.start()
            second.join(15)
            release.set()
            first.join(15)

        publish.assert_called_once()
        self.assertEqual(results, {"first": "Published via Ayrshare: INSTAGRAM_REELS", "second": "Locked"})
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PUBLISHED)

    def test_without_a_profile_nothing_is_locked_for_long(self):
        take_social_profile(self.workspace)
        post = self.make_post()
        upload_to_social_network.apply(args=[str(post.pk)])
        from django.core.cache import caches

        self.assertIsNone(caches["locks"].get(f"lock_post_publish_{post.pk}"))


class ProfileBeingCreatedTests(PublisherHarness, TestCase):
    """A SocialProfile row without its provider_ref (being created) is not an account yet, anywhere."""

    def test_a_profile_without_its_key_is_not_an_account(self):
        SocialProfile.objects.filter(workspace=self.workspace).update(provider_ref="")
        post = self.make_post()
        with patch(PUBLISH) as publish:
            self.run_task(post)
        publish.assert_not_called()
        self.assertEqual(post.error_log, "account_not_connected (no profile)")

        api = APIClient()
        api.force_authenticate(self.user)
        headers = {"HTTP_X_WORKSPACE_ID": str(self.workspace.pk)}
        response = api.post(reverse("scheduled-post-list"), {
            "video_clip": str(self.clip.pk), "platform": "INSTAGRAM_REELS",
        }, format="json", **headers)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "account_not_connected"))
        self.assertFalse(api.get("/api/v1/me/", **headers).json()["onboarding"]["has_social_account"])

        SocialProfile.objects.filter(workspace=self.workspace).update(provider_ref="KEY")
        self.assertTrue(api.get("/api/v1/me/", **headers).json()["onboarding"]["has_social_account"])
