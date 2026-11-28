"""
The Upload-Post provider against Upload-Post's documented answers (no network:
requests.request is replaced, and so is the clock of the status checks). What
it sends, how it reads an answer (the network's own result decides), how it
waits for an upload that goes on in the background, and which of our errors
each answer is.
"""
from http.client import RemoteDisconnected
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from apps.integrations import social
from apps.integrations.social import (
    PUBLISH_LOCK_TTL_SECONDS,
    PUBLISH_TIMEOUT_SECONDS,
    AccountStatus,
    OutcomeUnknownError,
    PermanentError,
    PublishRequest,
    RetryableError,
    TikTokOptions,
)
from apps.integrations.social import upload_post
from apps.integrations.social.base import ACCOUNTS_TIMEOUT_SECONDS
from apps.integrations.social.upload_post import UploadPostProvider

MODULE = "apps.integrations.social.upload_post"
REQUEST = f"{MODULE}.requests.request"
BASE = "https://api.upload-post.com"
PROFILE = SimpleNamespace(provider="upload_post", provider_ref="oc-test-ws-1")
KEY = "onecreator-post-7-v0"
WORDS = "Texto del proveedor que puede citar el caption"


def answer(status=200, body=None, headers=None):
    response = MagicMock(status_code=status, headers=headers or {})
    if body is None:
        response.json.side_effect = ValueError("not JSON")
    else:
        response.json.return_value = body
    return response


def ok(network, **entry):
    return {"success": True, "request_id": "up-req-1", "results": {network: {"success": True, **entry}}}


def failed(network, code, **entry):
    return {"success": True, "request_id": "up-req-1", "results": {network: {
        "success": False, "error": WORDS, "error_code": code, **entry,
    }}}


def status(state, *entries):
    return {"request_id": "up-req-1", "status": state, "results": list(entries)}


def tiktok_request(text="Hola\n\n#a", **options):
    return PublishRequest(
        platform="TIKTOK", media_url="https://r2.test/clip.mp4?X-Amz-Signature=abc", text=text,
        title="Clip", idempotency_key=KEY,
        tiktok=TikTokOptions(**{"privacy_level": "PUBLIC_TO_EVERYONE", **options}),
    )


def request_for(platform, **fields):
    return PublishRequest(**{
        "platform": platform, "media_url": "https://r2.test/clip.mp4", "text": "Hola", "title": "Mi clip",
        "idempotency_key": KEY, **fields,
    })


def fields_of(call):
    """The multipart fields of a call, as {name: value} (each one is sent as (None, value))."""
    return {name: value for name, (filename, value) in call.kwargs["files"]}


class Clock:
    """A clock that moves only when the provider sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class ProviderTestCase(SimpleTestCase):
    def setUp(self):
        self.clock = Clock()
        for name, value in (("_clock", self.clock), ("_sleep", self.clock.sleep)):
            patcher = patch(f"{MODULE}.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, request, *responses):
        """Publishes with these answers in order: the upload's, then each status read's."""
        with patch(REQUEST, side_effect=list(responses)) as sent:
            result = UploadPostProvider().publish(PROFILE, request)
        return result, sent

    def error_of(self, request, *responses):
        with self.assertRaises((PermanentError, RetryableError, OutcomeUnknownError)) as caught:
            self.publish(request, *responses)
        return caught.exception


@override_settings(UPLOAD_POST_API_KEY="api-key")
class PublishRequestTests(ProviderTestCase):
    def test_what_a_tiktok_post_sends(self):
        _, sent = self.publish(tiktok_request(), answer(200, ok("tiktok", url="https://www.tiktok.com/@a/video/1")))

        call = sent.call_args
        self.assertEqual(call.args, ("post", f"{BASE}/api/upload"))
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Apikey api-key", "Idempotency-Key": KEY})
        self.assertEqual(call.kwargs["timeout"], upload_post.UPLOAD_TIMEOUT_SECONDS)
        self.assertIsNone(call.kwargs["json"])
        self.assertEqual(fields_of(call), {
            "user": "oc-test-ws-1", "platform[]": "tiktok",
            "video": "https://r2.test/clip.mp4?X-Amz-Signature=abc",
            # Ours: its status can be read even when the answer is lost.
            "request_id": KEY,
            "title": "Hola\n\n#a", "tiktok_title": "Hola\n\n#a",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_comment": "true", "disable_duet": "true", "disable_stitch": "true",
            "brand_organic_toggle": "false", "brand_content_toggle": "false",
            "disable_inbox_fallback": "true",
        })
        # Every field goes as a multipart part, without a file name.
        self.assertTrue(all(filename is None for _, (filename, _) in call.kwargs["files"]))

    def test_tiktok_options_are_mapped(self):
        for level in social.TIKTOK_PRIVACY_LEVELS:
            with self.subTest(level=level):
                _, sent = self.publish(tiktok_request(privacy_level=level), answer(200, ok("tiktok")))
                self.assertEqual(fields_of(sent.call_args)["privacy_level"], level)  # the same values, 1:1

        _, sent = self.publish(
            tiktok_request(allow_comment=True, allow_duet=False, allow_stitch=True, brand_organic=True, branded_content=True),
            answer(200, ok("tiktok")),
        )
        fields = fields_of(sent.call_args)
        self.assertEqual(
            {name: fields[name] for name in ("disable_comment", "disable_duet", "disable_stitch",
                                             "brand_organic_toggle", "brand_content_toggle")},
            {"disable_comment": "false", "disable_duet": "true", "disable_stitch": "false",
             "brand_organic_toggle": "true", "brand_content_toggle": "true"},
        )

    def test_youtube_gets_the_clip_title_and_the_text_as_description(self):
        _, sent = self.publish(request_for("YOUTUBE_SHORTS", title="  <La> remontada  "), answer(200, ok("youtube")))
        fields = fields_of(sent.call_args)
        self.assertEqual((fields["title"], fields["youtube_title"]), ("La remontada", "La remontada"))
        self.assertEqual((fields["youtube_description"], fields["privacyStatus"]), ("Hola", "public"))
        self.assertNotIn("privacy_level", fields)

        _, sent = self.publish(request_for("YOUTUBE_SHORTS", title="ñ" * 150, text=""), answer(200, ok("youtube")))
        fields = fields_of(sent.call_args)
        self.assertEqual(fields["title"], "ñ" * 100)
        self.assertNotIn("youtube_description", fields)

    def test_instagram_takes_the_text_as_its_caption(self):
        _, sent = self.publish(request_for("INSTAGRAM_REELS"), answer(200, ok("instagram")))
        fields = fields_of(sent.call_args)
        self.assertEqual((fields["platform[]"], fields["instagram_title"], fields["media_type"]), ("instagram", "Hola", "REELS"))
        self.assertNotIn("privacy_level", fields)

    def test_an_empty_text_makes_nothing_up(self):
        """TikTok's caption falls back to `title`: without a text there is no title either."""
        _, sent = self.publish(tiktok_request(text=""), answer(200, ok("tiktok")))
        fields = fields_of(sent.call_args)
        self.assertNotIn("title", fields)
        self.assertNotIn("tiktok_title", fields)

    def test_a_tiktok_post_without_options_is_never_sent(self):
        with patch(REQUEST) as sent, self.assertRaises(PermanentError) as caught:
            UploadPostProvider().publish(PROFILE, request_for("TIKTOK"))
        self.assertEqual(caught.exception.code, "tiktok_options_missing")
        sent.assert_not_called()

    def test_without_a_profile_nothing_is_sent(self):
        empty = SimpleNamespace(provider="upload_post", provider_ref="")
        provider = UploadPostProvider()
        calls = {
            "publish": lambda: provider.publish(empty, tiktok_request()),
            "accounts": lambda: provider.accounts(empty),
            "disconnect": lambda: provider.disconnect(empty, "TIKTOK"),
            "connect_url": lambda: provider.connect_url(empty, "TIKTOK"),
        }
        with patch(REQUEST) as sent:
            for name, call in calls.items():
                with self.subTest(call=name), self.assertRaises(PermanentError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "account_not_connected")
            provider.delete_profile(empty)  # nothing to delete: done, without a call
        sent.assert_not_called()

    @override_settings(UPLOAD_POST_API_KEY=None)
    def test_without_the_api_key_nothing_is_sent(self):
        with patch(REQUEST) as sent, self.assertRaises(PermanentError) as caught:
            UploadPostProvider().publish(PROFILE, tiktok_request())
        self.assertEqual(caught.exception.code, "provider_not_configured")
        sent.assert_not_called()

    @override_settings(UPLOAD_POST_BASE_URL="http://127.0.0.1:8791/")
    def test_the_base_url_comes_from_the_setting(self):
        _, sent = self.publish(tiktok_request(), answer(200, ok("tiktok")))
        self.assertEqual(sent.call_args.args[1], "http://127.0.0.1:8791/api/upload")


@override_settings(UPLOAD_POST_API_KEY="api-key")
class PublishAnswerTests(ProviderTestCase):
    def test_a_published_post_keeps_its_link_and_id(self):
        result, _ = self.publish(
            request_for("INSTAGRAM_REELS"),
            answer(200, ok("instagram", url="https://www.instagram.com/reel/abc/", container_id="1789")),
        )
        self.assertEqual((result.external_id, result.post_url, result.pending),
                         ("up-req-1", "https://www.instagram.com/reel/abc/", False))

    def test_without_its_request_id_the_id_is_ours(self):
        body = {"success": True, "results": {"tiktok": {"success": True, "publish_id": "v_pub_1"}}}
        result, _ = self.publish(tiktok_request(), answer(200, body))
        self.assertEqual((result.external_id, result.post_url, result.pending), (KEY, None, True))

    def test_a_tiktok_draft_in_the_inbox_is_out_without_a_link(self):
        body = ok("tiktok", url="Video sent to Inbox (No Public URL)", fallback_to_inbox=True)
        result, _ = self.publish(tiktok_request(), answer(200, body))
        self.assertEqual((result.post_url, result.pending), (None, True))

    def test_the_network_result_decides(self):
        # A request that went through with the network failed is not a success.
        error = self.error_of(tiktok_request(), answer(200, failed("tiktok", "content_rejected")))
        self.assertIsInstance(error, PermanentError)
        self.assertEqual(error.code, "provider_rejected")
        # Only our network's entry counts.
        other = {"success": True, "request_id": "up-req-1", "results": {
            "tiktok": {"success": True, "url": "https://www.tiktok.com/@a/video/1"},
            "linkedin": {"success": False, "error": WORDS},
        }}
        result, _ = self.publish(tiktok_request(), answer(200, other))
        self.assertEqual(result.post_url, "https://www.tiktok.com/@a/video/1")

    def test_upload_post_codes_become_our_codes(self):
        cases = {
            "account_reauth_required": (PermanentError, "account_expired"),
            "account_permission_denied": (PermanentError, "account_expired"),
            "daily_cap_reached": (PermanentError, "quota_exceeded"),
            "monthly_limit_reached": (PermanentError, "quota_exceeded"),
            "reached_active_user_cap": (PermanentError, "quota_exceeded"),
            "tiktok_privacy_unavailable": (PermanentError, "tiktok_options_missing"),
            "content_duplicate": (PermanentError, "duplicate_content"),
            "profile_platform_mapping_invalid": (PermanentError, "account_not_connected"),
            "content_rejected": (PermanentError, "provider_rejected"),
            "tiktok_media_rejected": (PermanentError, "provider_rejected"),
            # "Check the account before posting again": it may be out.
            "upload_interrupted": (OutcomeUnknownError, "outcome_unknown"),
            "tiktok_publish_failed": (OutcomeUnknownError, "outcome_unknown"),
            # "The platform failed or timed out": it may be out, and the same key only answers this job.
            "platform_unavailable": (OutcomeUnknownError, "outcome_unknown"),
            # Not classified, or a code added later: never a refusal ("Reintentar" would send a second copy).
            "unknown_error": (OutcomeUnknownError, "outcome_unknown"),
            "a_code_added_later": (OutcomeUnknownError, "outcome_unknown"),
        }
        for code, (kind, ours) in cases.items():
            with self.subTest(code=code):
                error = self.error_of(tiktok_request(), answer(200, failed("tiktok", code)))
                self.assertIsInstance(error, kind)
                self.assertEqual(error.code, ours)
                self.assertEqual(error.detail, f"upload-post code={code}")
                self.assertNotIn("caption", str(error))  # never the provider's words

    def test_a_failure_without_a_code_is_unknown(self):
        body = {"success": True, "request_id": "up-req-1", "results": {"tiktok": {"success": False, "error": WORDS}}}
        error = self.error_of(tiktok_request(), answer(200, body))
        self.assertEqual((type(error), error.code), (OutcomeUnknownError, "outcome_unknown"))

    def test_a_retry_later_refusal_is_retried_with_a_new_key_once_its_status_says_failed(self):
        """
        Nothing went out, and Upload-Post is done with it: this job's key answers the same failure for
        24 hours, so the next attempt needs a new one. Until its status says `failed` (it may be
        `retryable`: Upload-Post retrying by itself) no new key.
        """
        for code in ("platform_rate_limited", "platform_integration_paused", "cost_cap_reached", "media_download_failed"):
            with self.subTest(code=code):
                retrying = {"platform": "tiktok", "success": False, "status": "retryable", "error_code": code}
                done = {"platform": "tiktok", "success": False, "status": "failed", "error_code": code}
                error = self.error_of(
                    tiktok_request(), answer(200, failed("tiktok", code)),
                    answer(200, status("in_progress", retrying)), answer(200, status("failed", done)),
                )
                self.assertIsInstance(error, RetryableError)
                self.assertEqual((error.key_spent, error.maybe_sent), (True, False))

    def test_a_retry_later_refusal_that_upload_post_retries_is_published(self):
        retrying = {"platform": "tiktok", "success": False, "status": "retryable", "error_code": "platform_rate_limited"}
        done = {"platform": "tiktok", "success": True, "status": "completed", "post_url": "https://www.tiktok.com/@a/video/3"}
        result, sent = self.publish(
            tiktok_request(), answer(200, failed("tiktok", "platform_rate_limited")),
            answer(200, status("in_progress", retrying)), answer(200, status("completed", done)),
        )
        self.assertEqual(result.post_url, "https://www.tiktok.com/@a/video/3")
        self.assertEqual([call.args[0] for call in sent.call_args_list].count("post"), 1)

    def test_a_retry_later_refusal_never_said_failed_is_unknown(self):
        retrying = {"platform": "tiktok", "success": False, "status": "retryable", "error_code": "platform_rate_limited"}
        error = self.error_of(
            tiktok_request(), answer(200, failed("tiktok", "platform_rate_limited")),
            *[answer(200, status("in_progress", retrying))] * 40,
        )
        self.assertIsInstance(error, OutcomeUnknownError)
        # A finished request whose entry has no status of its own is not a `failed` network either.
        bare = {"platform": "tiktok", "success": False, "error_code": "platform_rate_limited"}
        error = self.error_of(
            tiktok_request(), answer(200, failed("tiktok", "platform_rate_limited")), answer(200, status("failed", bare)),
        )
        self.assertIsInstance(error, OutcomeUnknownError)

    def test_platform_unavailable_in_its_status_is_unknown(self):
        done = {"platform": "tiktok", "success": False, "status": "failed", "error_code": "platform_unavailable"}
        error = self.error_of(tiktok_request(), answer(200, BackgroundUploadTests.ASYNC), answer(200, status("failed", done)))
        self.assertEqual((type(error), error.code), (OutcomeUnknownError, "outcome_unknown"))

    def test_a_network_the_profile_has_no_account_for(self):
        skipped = failed("tiktok", "profile_platform_mapping_invalid", skipped=True, skip_reason="profile_platform_not_configured")
        self.assertEqual(self.error_of(tiktok_request(), answer(200, skipped)).code, "account_not_connected")
        none_valid = {"success": False, "message": WORDS, "invalid_platforms": {"tiktok": WORDS}}
        self.assertEqual(self.error_of(tiktok_request(), answer(400, none_valid)).code, "account_not_connected")
        no_profile = {"success": False, "message": WORDS, "error_code": "profile_not_found"}
        self.assertEqual(self.error_of(tiktok_request(), answer(400, no_profile)).code, "account_not_connected")

    def test_a_rate_limit_is_retried_with_its_wait(self):
        error = self.error_of(tiktok_request(), answer(429, {"success": False, "message": WORDS}, {"Retry-After": "120"}))
        self.assertIsInstance(error, RetryableError)
        self.assertEqual(error.retry_after, 120)
        # Refused before it was processed: the same key, never a spent one.
        self.assertEqual((error.key_spent, error.maybe_sent), (False, False))
        in_body = self.error_of(tiktok_request(), answer(429, {"success": False, "retry_after": 30}))
        self.assertEqual((type(in_body), in_body.retry_after), (RetryableError, 30))

    def test_the_monthly_allowance_is_permanent(self):
        usage = {"success": False, "message": WORDS, "usage": {"count": 10, "limit": 10}}
        self.assertEqual(self.error_of(tiktok_request(), answer(429, usage)).code, "quota_exceeded")
        coded = {"success": False, "message": WORDS, "error_code": "monthly_limit_reached"}
        self.assertEqual(self.error_of(tiktok_request(), answer(429, coded)).code, "quota_exceeded")

    def test_a_503_that_says_when_is_retried(self):
        error = self.error_of(tiktok_request(), answer(503, {"success": False}, {"Retry-After": "60"}))
        self.assertIsInstance(error, RetryableError)
        self.assertEqual((error.retry_after, error.key_spent, error.maybe_sent), (60, False, False))

    def test_a_5xx_without_an_explanation_is_retried_with_the_same_key(self):
        """The same key answers the original job: a retry never publishes a second copy."""
        for code in (500, 502, 503):
            with self.subTest(status=code):
                error = self.error_of(tiktok_request(), answer(code, None))
                self.assertIsInstance(error, RetryableError)
                self.assertEqual((error.key_spent, error.maybe_sent), (False, True))
        # A documented code inside a 5xx is still that code.
        coded = {"success": False, "error_code": "account_reauth_required"}
        self.assertEqual(self.error_of(tiktok_request(), answer(500, coded)).code, "account_expired")

    def test_a_key_still_in_use_waits_for_its_job(self):
        body = {"success": False, "message": WORDS, "error_code": "IDEMPOTENCY_KEY_IN_USE"}
        error = self.error_of(tiktok_request(), answer(409, body))
        self.assertIsInstance(error, RetryableError)
        self.assertEqual((error.maybe_sent, error.key_spent), (True, False))

    def test_an_invalid_api_key_is_ours_to_fix(self):
        self.assertEqual(self.error_of(tiktok_request(), answer(401, {"success": False})).code, "provider_not_configured")

    def test_another_refusal_is_rejected(self):
        error = self.error_of(tiktok_request(), answer(403, {"success": False, "message": WORDS}))
        self.assertEqual((type(error), error.code, error.detail), (PermanentError, "provider_rejected", "upload-post http=403"))

    def test_logs_carry_codes_not_words(self):
        with patch(REQUEST, return_value=answer(200, failed("tiktok", "account_reauth_required"))), \
                self.assertLogs(MODULE, level="INFO") as logs, self.assertRaises(PermanentError):
            UploadPostProvider().publish(PROFILE, tiktok_request())
        output = "\n".join(logs.output)
        self.assertIn("code=account_expired", output)
        self.assertIn("upload-post code=account_reauth_required", output)
        for secret in (WORDS, "api-key", "Hola", "r2.test"):
            self.assertNotIn(secret, output)


@override_settings(UPLOAD_POST_API_KEY="api-key")
class BackgroundUploadTests(ProviderTestCase):
    """An upload Upload-Post finishes later: its status, read again and again, says what happened."""

    ASYNC = {"success": True, "message": "Upload initiated successfully in background.", "request_id": "up-req-1",
             "total_platforms": 1}
    QUEUED = {"platform": "tiktok", "success": True, "message": "Queued"}
    DONE = {"platform": "tiktok", "success": True, "status": "completed",
            "post_url": "https://www.tiktok.com/@a/video/9"}

    def test_it_is_published_once_its_status_says_so(self):
        result, sent = self.publish(
            tiktok_request(),
            answer(200, self.ASYNC),
            answer(200, status("in_progress", self.QUEUED)),
            answer(200, status("completed", self.DONE)),
        )
        self.assertEqual((result.external_id, result.post_url, result.pending),
                         ("up-req-1", "https://www.tiktok.com/@a/video/9", False))
        upload, *reads = sent.call_args_list
        self.assertEqual(upload.args, ("post", f"{BASE}/api/upload"))
        for read in reads:
            self.assertEqual(read.args, ("get", f"{BASE}/api/uploadposts/status"))
            self.assertEqual(read.kwargs["params"], {"request_id": "up-req-1"})
            self.assertEqual(read.kwargs["headers"], {"Authorization": "Apikey api-key"})
        self.assertEqual(self.clock.slept, [upload_post.STATUS_POLL_SECONDS] * 2)

    def test_a_queued_entry_that_says_success_is_not_published(self):
        """The docs show `success: true, message: "Queued"`: never out until its status (or the request's) is final."""
        reads = [answer(200, status("in_progress", self.QUEUED)) for _ in range(40)]
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), *reads)
        self.assertIsInstance(error, OutcomeUnknownError)
        self.assertIn("status=in_progress", error.detail)
        self.assertIn("request_id=up-req-1", error.detail)

    def test_the_whole_publish_fits_in_the_lock(self):
        reads = [answer(200, status("processing")) for _ in range(40)]
        with patch(REQUEST, side_effect=[answer(200, self.ASYNC), *reads]) as sent, self.assertRaises(OutcomeUnknownError):
            UploadPostProvider().publish(PROFILE, tiktok_request())
        self.assertLessEqual(sum(self.clock.slept), PUBLISH_TIMEOUT_SECONDS[1])
        self.assertLess(PUBLISH_TIMEOUT_SECONDS[1] + PUBLISH_TIMEOUT_SECONDS[0] * 2, PUBLISH_LOCK_TTL_SECONDS)
        # Only reads after the upload: it is never sent again.
        self.assertEqual([call.args[0] for call in sent.call_args_list].count("post"), 1)
        for read in sent.call_args_list[1:]:
            self.assertLessEqual(read.kwargs["timeout"][1], upload_post.STATUS_TIMEOUT_SECONDS[1])

    def test_a_failed_status_is_the_networks_error(self):
        failure = {"platform": "tiktok", "success": False, "status": "failed", "error_code": "account_reauth_required",
                   "error": WORDS}
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), answer(200, status("failed", failure)))
        self.assertEqual((type(error), error.code), (PermanentError, "account_expired"))
        skipped = {"platform": "tiktok", "success": False, "status": "skipped", "skipped": True,
                   "skip_reason": "profile_platform_not_configured"}
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), answer(200, status("completed", skipped)))
        self.assertEqual(error.code, "account_not_connected")

    def test_a_retryable_entry_is_waited_for(self):
        """`retryable`: Upload-Post tries it again by itself; not final."""
        retrying = {"platform": "tiktok", "success": False, "status": "retryable", "error_code": "platform_unavailable"}
        result, _ = self.publish(
            tiktok_request(), answer(200, self.ASYNC), answer(200, status("processing", retrying)),
            answer(200, status("completed", self.DONE)),
        )
        self.assertEqual(result.post_url, "https://www.tiktok.com/@a/video/9")

    def test_a_status_that_cannot_be_read_is_never_a_refusal(self):
        """The upload may be out: a failing status read is asked again, and at the end it is unknown."""
        cases = {
            "5xx": [answer(500, None)] * 40,
            "rate limit": [answer(429, None, {"Retry-After": "15"})] * 40,
            "not found yet": [answer(404, {"status": "not_found"})] * 40,
            "connection": [requests.exceptions.ConnectionError("reset")] * 40,
            "never connects": [requests.exceptions.ConnectTimeout("x")] * 40,
            "key refused": [answer(401, {"success": False})],
        }
        for name, reads in cases.items():
            with self.subTest(case=name):
                error = self.error_of(tiktok_request(), answer(200, self.ASYNC), *reads)
                self.assertIsInstance(error, OutcomeUnknownError)

    def test_an_answer_after_the_deadline_is_not_waited_for(self):
        """The upload itself used the whole time: unknown at once, without sleeping or reading."""
        def slow_upload(*args, **kwargs):
            self.clock.now += upload_post.PUBLISH_DEADLINE_SECONDS + 1
            return answer(200, self.ASYNC)

        with patch(REQUEST, side_effect=slow_upload) as sent, self.assertRaises(OutcomeUnknownError) as caught:
            UploadPostProvider().publish(PROFILE, tiktok_request())
        self.assertEqual((sent.call_count, self.clock.slept), (1, []))
        self.assertIn("status=none", caught.exception.detail)

    def test_a_failed_request_with_a_stale_success_is_not_published(self):
        """`failed` also means «no activity for over 1 hour»: a «Queued» entry with success: true left there."""
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), answer(200, status("failed", self.QUEUED)))
        self.assertIsInstance(error, OutcomeUnknownError)
        # A completed request makes it final.
        result, _ = self.publish(tiktok_request(), answer(200, self.ASYNC), answer(200, status("completed", self.QUEUED)))
        self.assertTrue(result.pending)

    def test_a_completed_entry_without_success_is_unknown(self):
        odd = {"platform": "tiktok", "success": False, "status": "completed"}
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), answer(200, status("completed", odd)))
        self.assertIsInstance(error, OutcomeUnknownError)

    def test_no_status_read_after_the_deadline(self):
        """The last wait reaches the deadline: no read after it, however short."""
        def server(method, url, **kwargs):
            if method == "post":
                self.clock.now += upload_post.PUBLISH_DEADLINE_SECONDS - 5  # the upload took almost all of it
                return answer(200, self.ASYNC)
            return answer(200, status("completed", self.DONE))

        with patch(REQUEST, side_effect=server) as sent, self.assertRaises(OutcomeUnknownError):
            UploadPostProvider().publish(PROFILE, tiktok_request())
        self.assertEqual((sent.call_count, self.clock.slept), (1, [5]))

    def test_finished_without_the_network_is_unknown(self):
        other = {"platform": "x", "success": True, "status": "completed"}
        error = self.error_of(tiktok_request(), answer(200, self.ASYNC), answer(200, status("completed", other)))
        self.assertIsInstance(error, OutcomeUnknownError)

    def test_a_queued_entry_in_the_upload_answer_is_waited_for(self):
        queued = {"success": True, "request_id": "up-req-1", "results": {"tiktok": {"success": True, "status": "queued"}}}
        result, _ = self.publish(tiktok_request(), answer(200, queued), answer(200, status("completed", self.DONE)))
        self.assertFalse(result.pending)

    def test_a_504_is_settled_by_its_status(self):
        """The docs: after a 504, do not send it again; look it up by your own request_id."""
        result, sent = self.publish(
            tiktok_request(), answer(504, None), answer(200, status("completed", self.DONE)),
        )
        self.assertEqual(result.post_url, "https://www.tiktok.com/@a/video/9")
        self.assertEqual(sent.call_args_list[1].kwargs["params"], {"request_id": KEY})
        # Unconfirmed, it stays unknown: never retried.
        reads = [answer(404, {"status": "not_found"})] * 40
        error = self.error_of(tiktok_request(), answer(504, None), *reads)
        self.assertIsInstance(error, OutcomeUnknownError)
        self.assertIn("upload-post http=504", error.detail)

    def test_a_request_sent_without_an_answer_is_settled_by_its_status(self):
        closed = requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", RemoteDisconnected("closed")))
        cases = {
            "closed after sending": closed,
            "read timeout": requests.exceptions.ReadTimeout("x"),
            "answer cut": requests.exceptions.ChunkedEncodingError("cut"),
        }
        for name, exc in cases.items():
            with self.subTest(case=name):
                result, sent = self.publish(tiktok_request(), exc, answer(200, status("completed", self.DONE)))
                self.assertFalse(result.pending)
                self.assertEqual(sent.call_args_list[1].kwargs["params"], {"request_id": KEY})
                error = self.error_of(tiktok_request(), exc, *[answer(200, status("pending"))] * 40)
                self.assertIsInstance(error, OutcomeUnknownError)

    def test_a_request_that_never_left_is_retried_without_a_status_check(self):
        refused = MaxRetryError(None, "/api/upload", NewConnectionError(None, "Connection refused"))
        for exc in (requests.exceptions.ConnectTimeout("x"), requests.exceptions.ConnectionError(refused)):
            with self.subTest(error=type(exc).__name__), patch(REQUEST, side_effect=[exc]) as sent:
                with self.assertRaises(RetryableError) as caught:
                    UploadPostProvider().publish(PROFILE, tiktok_request())
                self.assertEqual((caught.exception.key_spent, caught.exception.maybe_sent), (False, False))
                self.assertEqual(sent.call_count, 1)


@override_settings(UPLOAD_POST_API_KEY="api-key", SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
class ProfileTests(SimpleTestCase):
    WORKSPACE = SimpleNamespace(pk="5b0e2f7a-9d1c-4c5e-8f00-123456789abc")
    USERNAME = "oc-test-5b0e2f7a-9d1c-4c5e-8f00-123456789abc"

    def setUp(self):
        cache.clear()  # TikTok's settings are kept there

    def test_create_profile(self):
        body = {"success": True, "profile": {"username": self.USERNAME, "social_accounts": {}}}
        with patch(REQUEST, return_value=answer(201, body)) as sent:
            created = UploadPostProvider().create_profile(self.WORKSPACE)
        self.assertEqual((created.ref, created.provider_id), (self.USERNAME, self.USERNAME))
        self.assertEqual(sent.call_args.args, ("post", f"{BASE}/api/uploadposts/users"))
        self.assertEqual(sent.call_args.kwargs["json"], {"username": self.USERNAME})
        self.assertEqual(sent.call_args.kwargs["headers"], {"Authorization": "Apikey api-key"})

    def test_a_username_that_exists_is_the_same_profile(self):
        """409: an earlier creation whose answer was lost. Creating again never makes a second profile."""
        with patch(REQUEST, return_value=answer(409, {"success": False, "message": "Username already in use"})):
            created = UploadPostProvider().create_profile(self.WORKSPACE)
        self.assertEqual(created.ref, self.USERNAME)

    @override_settings(SOCIAL_PROFILE_TITLE_PREFIX="oc.staging 2")
    def test_the_username_keeps_to_upload_posts_characters(self):
        self.assertEqual(upload_post.profile_username(self.WORKSPACE), "oc-staging-2-5b0e2f7a-9d1c-4c5e-8f00-123456789abc")

    def test_create_profile_errors(self):
        limit = {"success": False, "message": WORDS, "error_code": "PROFILE_LIMIT_REACHED"}
        with patch(REQUEST, return_value=answer(403, limit)), self.assertRaises(PermanentError) as caught:
            UploadPostProvider().create_profile(self.WORKSPACE)
        self.assertEqual((caught.exception.code, caught.exception.detail),
                         ("provider_rejected", "upload-post http=403 code=profile_limit_reached"))
        # Without an answer it may exist: asking again is safe (409 is the same profile).
        with patch(REQUEST, side_effect=requests.exceptions.ReadTimeout("x")), self.assertRaises(RetryableError) as caught:
            UploadPostProvider().create_profile(self.WORKSPACE)
        self.assertTrue(caught.exception.maybe_sent)

    def test_connect_url(self):
        body = {"success": True, "duration": "48h", "access_url": "https://app.upload-post.com/connect?token=jwt"}
        with patch(REQUEST, return_value=answer(200, body)) as sent:
            url = UploadPostProvider().connect_url(PROFILE, "INSTAGRAM_REELS", "https://app.test/ajustes/redes?conectada=1")
        self.assertEqual(url, "https://app.upload-post.com/connect?token=jwt")
        self.assertEqual(sent.call_args.args, ("post", f"{BASE}/api/uploadposts/users/generate-jwt"))
        self.assertEqual(sent.call_args.kwargs["json"], {
            "username": "oc-test-ws-1", "platforms": ["instagram"], "language": "es",
            "connect_title": upload_post.LINKING_PAGE_TITLE, "show_calendar": False,
            "redirect_url": "https://app.test/ajustes/redes?conectada=1",
            "redirect_button_text": upload_post.LINKING_PAGE_BACK,
        })
        with patch(REQUEST, return_value=answer(200, body)) as sent:
            UploadPostProvider().connect_url(PROFILE, "TIKTOK")
        self.assertNotIn("redirect_url", sent.call_args.kwargs["json"])
        self.assertEqual(sent.call_args.kwargs["json"]["platforms"], ["tiktok"])

    def test_the_link_of_a_lost_profile(self):
        gone = {"success": False, "error_code": "PROFILE_NOT_FOUND", "message": WORDS}
        with patch(REQUEST, return_value=answer(404, gone)), self.assertRaises(PermanentError) as caught:
            UploadPostProvider().connect_url(PROFILE, "TIKTOK")
        self.assertEqual(caught.exception.code, "profile_not_found")

    def test_accounts(self):
        user = {"success": True, "profile": {"username": "oc-test-ws-1", "social_accounts": {
            "tiktok": {"username": "-000g91dgwX", "handle": "estudionorte", "display_name": "Estudio Norte",
                       "reauth_required": False, "capabilities": ["video_privacy"]},
            "instagram": {"username": "1789", "handle": "@estudio.norte", "reauth_required": True},
            "youtube": None,
            "facebook": {"handle": "otra"},
        }}}
        settings_body = {"success": True, "privacy_level_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                         "max_video_post_duration_sec": 600, "comment_disabled": True, "duet_disabled": False,
                         "stitch_disabled": False}
        with patch(REQUEST, side_effect=[answer(200, user), answer(200, settings_body)]) as sent:
            accounts = UploadPostProvider().accounts(PROFILE)

        read, creator = sent.call_args_list
        self.assertEqual(read.args, ("get", f"{BASE}/api/uploadposts/users/oc-test-ws-1"))
        self.assertEqual(read.kwargs["timeout"], ACCOUNTS_TIMEOUT_SECONDS)
        self.assertEqual(creator.args, ("get", f"{BASE}/api/uploadposts/tiktok/settings"))
        self.assertEqual(creator.kwargs["params"], {"profile": "oc-test-ws-1"})
        self.assertEqual(creator.kwargs["timeout"], upload_post.TIKTOK_SETTINGS_TIMEOUT_SECONDS)
        self.assertEqual([(a.platform, a.status, a.username) for a in accounts], [
            # The public handle, never `username` (TikTok's opaque open_id).
            ("TIKTOK", AccountStatus.CONNECTED, "@estudionorte"),
            ("INSTAGRAM_REELS", AccountStatus.EXPIRED, "@estudio.norte"),
            ("YOUTUBE_SHORTS", AccountStatus.DISCONNECTED, None),
        ])
        self.assertEqual(accounts[0].tiktok, {
            "privacy_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
            "comment_disabled": True, "duet_disabled": False, "stitch_disabled": False,
        })
        self.assertIsNone(accounts[1].tiktok)

    def test_tiktok_options_only_in_our_terms(self):
        user = {"success": True, "profile": {"social_accounts": {"tiktok": {"handle": "a"}}}}
        cases = {
            "legacy and unknown": ({"success": True, "privacy_level_options": ["FOLLOWER_ONLY", "EVERYONE_ON_MARS", "SELF_ONLY"]},
                                   ["FOLLOWER_OF_CREATOR", "SELF_ONLY"]),
            "none": ({"success": True, "privacy_level_options": []}, None),
        }
        for name, (body, expected) in cases.items():
            cache.clear()
            with self.subTest(case=name), patch(REQUEST, side_effect=[answer(200, user), answer(200, body)]):
                tiktok = UploadPostProvider().accounts(PROFILE)[0]
            self.assertEqual(tiktok.status, AccountStatus.CONNECTED)
            self.assertEqual(tiktok.tiktok and tiktok.tiktok["privacy_options"], expected)

    def test_without_tiktoks_settings_the_account_stays(self):
        """The creator info is an extra: its failure never fails the accounts (the API then offers the default)."""
        user = {"success": True, "profile": {"social_accounts": {"tiktok": {"handle": "a"}}}}
        for failure in (answer(502, {"success": False, "message": WORDS}), requests.exceptions.ReadTimeout("x")):
            cache.clear()
            with self.subTest(failure=failure), patch(REQUEST, side_effect=[answer(200, user), failure]):
                tiktok = UploadPostProvider().accounts(PROFILE)[0]
            self.assertEqual((tiktok.status, tiktok.tiktok), (AccountStatus.CONNECTED, None))
        # TikTok no longer takes the connection: reconnect.
        cache.clear()
        revoked = answer(400, {"success": False, "error_code": "tiktok_reconnect_required", "reauth_required": True})
        with patch(REQUEST, side_effect=[answer(200, user), revoked]):
            self.assertEqual(UploadPostProvider().accounts(PROFILE)[0].status, AccountStatus.EXPIRED)

    def test_tiktoks_settings_are_kept_for_a_while(self):
        """One more call to the accounts screen: at most every TIKTOK_SETTINGS_CACHE_SECONDS (a failure, a minute)."""
        user = {"success": True, "profile": {"social_accounts": {"tiktok": {"handle": "a"}}}}
        body = {"success": True, "privacy_level_options": ["SELF_ONLY"]}
        with patch(REQUEST, side_effect=[answer(200, user), answer(200, body), answer(200, user)]) as sent:
            first = UploadPostProvider().accounts(PROFILE)[0]
            again = UploadPostProvider().accounts(PROFILE)[0]
        self.assertEqual(first.tiktok, again.tiktok)
        self.assertEqual([call.args[1].rsplit("/", 1)[1] for call in sent.call_args_list], ["oc-test-ws-1", "settings", "oc-test-ws-1"])

        cache.clear()
        with patch(REQUEST, side_effect=[answer(200, user), answer(502, None), answer(200, user)]) as sent:
            UploadPostProvider().accounts(PROFILE)
            self.assertIsNone(UploadPostProvider().accounts(PROFILE)[0].tiktok)
        self.assertEqual(sent.call_count, 3)  # the failure is not asked again at once

    def test_connecting_or_disconnecting_forgets_tiktoks_settings(self):
        user = {"success": True, "profile": {"social_accounts": {"tiktok": {"handle": "a"}}}}
        body = {"success": True, "privacy_level_options": ["SELF_ONLY"]}
        link = {"success": True, "access_url": "https://app.upload-post.com/connect?token=x"}
        for name, change in (("connect", lambda p: p.connect_url(PROFILE, "TIKTOK")),
                             ("disconnect", lambda p: p.disconnect(PROFILE, "TIKTOK"))):
            cache.clear()
            with self.subTest(change=name), patch(REQUEST, side_effect=[
                answer(200, user), answer(200, body), answer(200, link if name == "connect" else {"success": True}),
                answer(200, user), answer(200, body),
            ]) as sent:
                provider = UploadPostProvider()
                provider.accounts(PROFILE)
                change(provider)
                provider.accounts(PROFILE)
            self.assertEqual(sent.call_count, 5)

    def test_an_expired_tiktok_does_not_ask_for_its_settings(self):
        user = {"success": True, "profile": {"social_accounts": {"tiktok": {"handle": "a", "reauth_required": True}}}}
        with patch(REQUEST, side_effect=[answer(200, user)]) as sent:
            self.assertEqual(UploadPostProvider().accounts(PROFILE)[0].status, AccountStatus.EXPIRED)
        self.assertEqual(sent.call_count, 1)

    def test_accounts_of_a_lost_profile(self):
        with patch(REQUEST, return_value=answer(404, {"success": False, "message": "Profile not found"})), \
                self.assertRaises(PermanentError) as caught:
            UploadPostProvider().accounts(PROFILE)
        self.assertEqual(caught.exception.code, "profile_not_found")

    def test_disconnect_and_delete(self):
        with patch(REQUEST, return_value=answer(200, {"success": True})) as sent:
            UploadPostProvider().disconnect(PROFILE, "YOUTUBE_SHORTS")
        self.assertEqual(sent.call_args.args, ("delete", f"{BASE}/api/uploadposts/users/social"))
        self.assertEqual(sent.call_args.kwargs["json"], {"profile_username": "oc-test-ws-1", "social_platform": "youtube"})

        with patch(REQUEST, return_value=answer(404, {"success": False})):
            UploadPostProvider().disconnect(PROFILE, "YOUTUBE_SHORTS")  # never linked: not an error
            UploadPostProvider().delete_profile(PROFILE)  # already gone: not an error
        with patch(REQUEST, return_value=answer(200, {"success": True})) as sent:
            UploadPostProvider().delete_profile(PROFILE)
        self.assertEqual(sent.call_args.args, ("delete", f"{BASE}/api/uploadposts/users"))
        self.assertEqual(sent.call_args.kwargs["json"], {"username": "oc-test-ws-1"})
        with patch(REQUEST, return_value=answer(500, None)), self.assertRaises(RetryableError):
            UploadPostProvider().delete_profile(PROFILE)


def test_the_provider_is_known_by_its_name():
    assert isinstance(social.get_provider("upload_post"), UploadPostProvider)
    assert isinstance(social.provider_for(SimpleNamespace(provider="upload_post")), UploadPostProvider)


@pytest.mark.parametrize("base", ["https://api.upload-post.com", "HTTPS://api.upload-post.com"])
def test_with_https_the_linking_page_must_be_https(base):
    body = {"success": True, "access_url": "http://app.upload-post.com/connect?token=x"}
    with override_settings(UPLOAD_POST_API_KEY="api-key", UPLOAD_POST_BASE_URL=base), \
            patch(REQUEST, return_value=answer(200, body)), pytest.raises(PermanentError):
        UploadPostProvider().connect_url(PROFILE, "TIKTOK")
