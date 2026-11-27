"""
The Ayrshare provider against Ayrshare's documented answers (no network:
requests.request is replaced). What it sends, how it reads an answer (the body
decides, not the HTTP status alone), and which of our errors each answer is.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from apps.integrations.social import (
    PUBLISH_TIMEOUT_SECONDS,
    AccountStatus,
    OutcomeUnknownError,
    PermanentError,
    PublishRequest,
    RetryableError,
    TikTokOptions,
)
from apps.integrations.social.ayrshare import AyrshareProvider

REQUEST = "apps.integrations.social.ayrshare.requests.request"
PROFILE = SimpleNamespace(provider="ayrshare", provider_ref="PROFILE-KEY-1")


def answer(status=200, body=None, headers=None):
    response = MagicMock(status_code=status, headers=headers or {})
    if body is None:
        response.json.side_effect = ValueError("not JSON")
    else:
        response.json.return_value = body
    return response


def ok(platform, **line):
    return {"status": "success", "errors": [], "id": "ayr-post-1",
            "postIds": [{"status": "success", "platform": platform, **line}]}


def refused(code, platform="tiktok", **extra):
    return {"status": "error", "id": "ayr-post-1", "postIds": [], "errors": [
        {"action": "post", "status": "error", "code": code, "platform": platform,
         "message": "Texto del proveedor que puede citar el caption", **extra},
    ]}


def tiktok_request(**options):
    return PublishRequest(
        platform="TIKTOK", media_url="https://r2.test/clip.mp4?X-Amz-Signature=abc", text="Hola\n\n#a",
        title="Clip", idempotency_key="onecreator-post-1-v0",
        tiktok=TikTokOptions(**{"privacy_level": "PUBLIC_TO_EVERYONE", **options}),
    )


def request_for(platform, **fields):
    return PublishRequest(**{
        "platform": platform, "media_url": "https://r2.test/clip.mp4", "text": "Hola", "title": "Mi clip",
        "idempotency_key": "onecreator-post-1-v0", **fields,
    })


@override_settings(AYRSHARE_API_KEY="api-key", SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
class PublishRequestTests(SimpleTestCase):
    def publish(self, request, response=None):
        with patch(REQUEST, return_value=response or answer(200, ok("tiktok", id="pending"))) as sent:
            AyrshareProvider().publish(PROFILE, request)
        return sent.call_args

    def test_what_a_post_sends(self):
        call = self.publish(tiktok_request())

        method, url = call.args
        self.assertEqual((method, url), ("post", "https://api.ayrshare.com/api/post"))
        self.assertEqual(call.kwargs["headers"]["Profile-Key"], "PROFILE-KEY-1")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer api-key")
        self.assertEqual(call.kwargs["timeout"], PUBLISH_TIMEOUT_SECONDS)
        body = call.kwargs["json"]
        self.assertEqual(body["post"], "Hola\n\n#a")
        self.assertEqual(body["platforms"], ["tiktok"])
        self.assertEqual(body["mediaUrls"], ["https://r2.test/clip.mp4?X-Amz-Signature=abc"])
        self.assertIs(body["isVideo"], True)
        self.assertEqual(body["idempotencyKey"], "onecreator-post-1-v0")
        # The key travels in the header only, never in the body.
        self.assertNotIn("profileKeys", body)
        self.assertNotIn("PROFILE-KEY-1", str(body))

    def test_tiktok_options_are_mapped(self):
        visibility = {
            "PUBLIC_TO_EVERYONE": "public", "FOLLOWER_OF_CREATOR": "followers",
            "MUTUAL_FOLLOW_FRIENDS": "friends", "SELF_ONLY": "private",
        }
        for level, expected in visibility.items():
            with self.subTest(level=level):
                body = self.publish(tiktok_request(privacy_level=level)).kwargs["json"]
                self.assertEqual(body["tikTokOptions"]["visibility"], expected)

        options = self.publish(tiktok_request(allow_comment=True, allow_duet=False, allow_stitch=True,
                                              brand_organic=True)).kwargs["json"]["tikTokOptions"]
        self.assertEqual(options, {
            "visibility": "public", "disableComments": False, "disableDuet": True, "disableStitch": False,
            "isBrandOrganic": True, "isBrandedContent": False,
        })

    def test_a_tiktok_post_without_options_is_never_sent(self):
        with patch(REQUEST) as sent, self.assertRaises(PermanentError) as caught:
            AyrshareProvider().publish(PROFILE, request_for("TIKTOK"))
        self.assertEqual(caught.exception.code, "tiktok_options_missing")
        sent.assert_not_called()

    def test_youtube_gets_a_public_short_with_the_clip_title(self):
        body = self.publish(request_for("YOUTUBE_SHORTS", title="  <La> remontada  "), answer(200, ok("youtube"))).kwargs["json"]
        self.assertEqual(body["youTubeOptions"], {"title": "La remontada", "visibility": "public", "shorts": True})
        self.assertNotIn("tikTokOptions", body)

        long_title = self.publish(request_for("YOUTUBE_SHORTS", title="ñ" * 150), answer(200, ok("youtube"))).kwargs["json"]
        self.assertEqual(long_title["youTubeOptions"]["title"], "ñ" * 100)
        empty = self.publish(request_for("YOUTUBE_SHORTS", title=""), answer(200, ok("youtube"))).kwargs["json"]
        self.assertEqual(empty["youTubeOptions"]["title"], "Clip")

    def test_instagram_needs_no_options(self):
        body = self.publish(request_for("INSTAGRAM_REELS"), answer(200, ok("instagram"))).kwargs["json"]
        self.assertEqual(body["platforms"], ["instagram"])
        self.assertNotIn("tikTokOptions", body)
        self.assertNotIn("youTubeOptions", body)

    def test_without_a_profile_key_nothing_is_sent(self):
        """Ayrshare acts on the primary profile when the header is missing: never."""
        empty = SimpleNamespace(provider="ayrshare", provider_ref="")
        provider = AyrshareProvider()
        calls = {
            "publish": lambda: provider.publish(empty, tiktok_request()),
            "accounts": lambda: provider.accounts(empty),
            "disconnect": lambda: provider.disconnect(empty, "TIKTOK"),
            "connect_url": lambda: provider.connect_url(empty, "TIKTOK"),
        }
        with patch(REQUEST) as sent:
            for name, call in calls.items():
                with self.subTest(call=name):
                    with self.assertRaises(PermanentError) as caught:
                        call()
                    self.assertEqual(caught.exception.code, "account_not_connected")
            # Nothing to delete: done, without a call.
            provider.delete_profile(empty)
        sent.assert_not_called()

    @override_settings(AYRSHARE_API_KEY=None)
    def test_without_the_api_key_nothing_is_sent(self):
        with patch(REQUEST) as sent, self.assertRaises(PermanentError) as caught:
            AyrshareProvider().publish(PROFILE, tiktok_request())
        self.assertEqual(caught.exception.code, "provider_not_configured")
        sent.assert_not_called()


@override_settings(AYRSHARE_API_KEY="api-key")
class PublishAnswerTests(SimpleTestCase):
    def outcome(self, response, request=None):
        with patch(REQUEST, return_value=response):
            return AyrshareProvider().publish(PROFILE, request or tiktok_request())

    def error_of(self, response, request=None):
        with self.assertRaises((PermanentError, RetryableError, OutcomeUnknownError)) as caught:
            self.outcome(response, request)
        return caught.exception

    def test_a_published_post_keeps_its_link_and_id(self):
        result = self.outcome(
            answer(200, ok("instagram", id="1787", postUrl="https://www.instagram.com/reel/abc/")),
            request_for("INSTAGRAM_REELS"),
        )
        self.assertEqual((result.external_id, result.post_url, result.pending),
                         ("ayr-post-1", "https://www.instagram.com/reel/abc/", False))

    def test_tiktok_pending_is_published_without_a_link(self):
        result = self.outcome(answer(200, ok("tiktok", id="pending", idShare="v_pub_url~v2.1", isVideo=True)))
        self.assertEqual((result.external_id, result.post_url, result.pending), ("ayr-post-1", None, True))

    def test_the_answer_wrapped_in_posts_is_read(self):
        """With a Profile-Key, Ayrshare may answer one item per profile under `posts`."""
        wrapped = {"status": "success", "posts": [{**ok("youtube", id="3oQ", postUrl="https://youtu.be/3oQ"),
                                                   "refId": "ref-1", "profileTitle": "oc-1"}]}
        result = self.outcome(answer(200, wrapped), request_for("YOUTUBE_SHORTS"))
        self.assertEqual((result.external_id, result.post_url), ("ayr-post-1", "https://youtu.be/3oQ"))

        refused_wrapped = {"status": "error", "posts": [{**refused(156, "youtube"), "refId": "ref-1"}]}
        self.assertEqual(self.error_of(answer(400, refused_wrapped), request_for("YOUTUBE_SHORTS")).code,
                         "account_not_connected")

    def test_the_body_decides_over_the_http_status(self):
        # A 200 that says error is not a success.
        error = self.error_of(answer(200, refused(999)))
        self.assertIsInstance(error, PermanentError)
        self.assertEqual(error.code, "provider_rejected")
        # A 400 that carries the network's success line is a success.
        partial = {**refused(110, "twitter"), "postIds": [
            {"status": "success", "platform": "tiktok", "id": "pending"},
        ]}
        self.assertTrue(self.outcome(answer(400, partial)).pending)

    def test_ayrshare_codes_become_our_codes(self):
        cases = {
            156: "account_not_connected",
            144: "account_not_connected",
            161: "account_expired",
            129: "account_expired",
            137: "duplicate_content",
            999: "provider_rejected",
        }
        for code, ours in cases.items():
            with self.subTest(code=code):
                error = self.error_of(answer(400, refused(code)))
                self.assertIsInstance(error, PermanentError)
                self.assertEqual(error.code, ours)
                self.assertEqual(error.detail, f"ayrshare http=400 code={code}")
                self.assertNotIn("caption", str(error))  # never the provider's words

    def test_the_monthly_quota_is_permanent(self):
        body = {"action": "quota limit", "status": "error", "code": 106, "message": "You have exceeded your API quota"}
        error = self.error_of(answer(429, body))
        self.assertIsInstance(error, PermanentError)
        self.assertEqual(error.code, "quota_exceeded")

    def test_a_rate_limit_is_retried_with_its_wait(self):
        body = {"action": "rate limit", "status": "error", "code": 999, "message": "Too many requests"}
        error = self.error_of(answer(429, body, {"Retry-After": "120"}))
        self.assertIsInstance(error, RetryableError)
        self.assertEqual(error.retry_after, 120)
        self.assertTrue(error.key_spent)
        # Without a body to read, the same: retried, and the same key goes again.
        bare = self.error_of(answer(429, None))
        self.assertIsInstance(bare, RetryableError)
        self.assertFalse(bare.key_spent)

    def test_retry_available_is_retried_with_a_new_key(self):
        error = self.error_of(answer(503, refused(531, retryAvailable=True)))
        self.assertIsInstance(error, RetryableError)
        self.assertTrue(error.key_spent)

    def test_a_5xx_without_an_explanation_is_retried_with_the_same_key(self):
        for status in (500, 502, 503):
            with self.subTest(status=status):
                error = self.error_of(answer(status, None))
                self.assertIsInstance(error, RetryableError)
                self.assertFalse(error.key_spent)

    def test_a_504_or_an_unreadable_success_may_have_published(self):
        for response in (answer(504, None), answer(200, None), answer(200, {"unexpected": True})):
            with self.subTest(status=response.status_code):
                self.assertIsInstance(self.error_of(response), OutcomeUnknownError)

    def test_a_refused_repeated_key_may_have_published(self):
        body = refused(999, message="Duplicate idempotencyKey found for this profile")
        error = self.error_of(answer(400, body))
        self.assertIsInstance(error, OutcomeUnknownError)
        self.assertEqual(error.code, "outcome_unknown")

    def test_an_invalid_api_key_is_ours_to_fix(self):
        error = self.error_of(answer(401, {"action": "auth", "status": "error", "code": 999, "message": "bad key"}))
        self.assertEqual(error.code, "provider_not_configured")

    def test_transport_errors(self):
        cases = (
            (requests.exceptions.ConnectTimeout("x"), RetryableError),
            (requests.exceptions.ConnectionError("reset"), RetryableError),
            (requests.exceptions.ReadTimeout("x"), OutcomeUnknownError),
        )
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__), patch(REQUEST, side_effect=exc):
                with self.assertRaises(expected):
                    AyrshareProvider().publish(PROFILE, tiktok_request())

    def test_logs_carry_codes_not_words(self):
        with patch(REQUEST, return_value=answer(400, refused(156))), \
                self.assertLogs("apps.integrations.social.ayrshare", level="INFO") as logs, \
                self.assertRaises(PermanentError):
            AyrshareProvider().publish(PROFILE, tiktok_request())
        output = "\n".join(logs.output)
        self.assertIn("code=account_not_connected", output)
        self.assertIn("ayrshare http=400 code=156", output)
        for secret in ("Texto del proveedor", "PROFILE-KEY-1", "api-key", "Hola"):
            self.assertNotIn(secret, output)


@override_settings(AYRSHARE_API_KEY="api-key", SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
class ProfileTests(SimpleTestCase):
    def test_create_profile(self):
        workspace = SimpleNamespace(pk="5b0e-ws")
        body = {"status": "success", "title": "oc-test-5b0e-ws", "refId": "ref-9", "profileKey": "KEY-9"}
        with patch(REQUEST, return_value=answer(200, body)) as sent:
            created = AyrshareProvider().create_profile(workspace)

        self.assertEqual((created.ref, created.provider_id), ("KEY-9", "ref-9"))
        method, url = sent.call_args.args
        self.assertEqual((method, url), ("post", "https://api.ayrshare.com/api/profiles"))
        self.assertEqual(sent.call_args.kwargs["json"]["title"], "oc-test-5b0e-ws")
        self.assertNotIn("Profile-Key", sent.call_args.kwargs["headers"])

    def test_create_profile_errors(self):
        workspace = SimpleNamespace(pk="5b0e-ws")
        taken = {"action": "create", "status": "error", "code": 146, "message": "Profile title already exists."}
        with patch(REQUEST, return_value=answer(400, taken)), self.assertRaises(PermanentError) as caught:
            AyrshareProvider().create_profile(workspace)
        self.assertEqual(caught.exception.detail, "ayrshare http=400 code=146")
        # Without an answer it may exist: unknown, never retried blindly.
        with patch(REQUEST, side_effect=requests.exceptions.ReadTimeout("x")), self.assertRaises(OutcomeUnknownError):
            AyrshareProvider().create_profile(workspace)

    def test_connect_url(self):
        body = {"status": "success", "title": "t", "token": "ayr_ls_x", "url": "https://profile.ayrshare.com?session=ayr_ls_x",
                "expiresIn": "5m"}
        with patch(REQUEST, return_value=answer(200, body)) as sent:
            url = AyrshareProvider().connect_url(PROFILE, "INSTAGRAM_REELS", "https://app.test/ajustes/redes?conectada=1")

        self.assertEqual(url, "https://profile.ayrshare.com?session=ayr_ls_x")
        self.assertEqual(sent.call_args.args[1], "https://api.ayrshare.com/api/profiles/generateJWT")
        self.assertEqual(sent.call_args.kwargs["json"], {
            "profileKey": "PROFILE-KEY-1", "allowedSocial": ["instagram"], "language": "es",
            "redirect": "https://app.test/ajustes/redes?conectada=1",
        })

    def test_accounts(self):
        now = datetime.now(dt_timezone.utc)
        body = {
            "activeSocialAccounts": ["tiktok", "instagram", "facebook"],
            "displayNames": [
                {"platform": "tiktok", "username": "@estudionorte", "created": "2026-11-02T02:11:53.452Z",
                 "refreshDaysRemaining": 120, "refreshRequired": (now + timedelta(days=120)).isoformat()},
                {"platform": "instagram", "username": "estudio.norte", "created": "2026-11-09T20:36:58.659Z",
                 "refreshRequired": (now - timedelta(days=1)).isoformat()},
            ],
        }
        with patch(REQUEST, return_value=answer(200, body)) as sent:
            accounts = AyrshareProvider().accounts(PROFILE)

        self.assertEqual(sent.call_args.args, ("get", "https://api.ayrshare.com/api/user"))
        self.assertEqual(sent.call_args.kwargs["headers"]["Profile-Key"], "PROFILE-KEY-1")
        self.assertEqual([(a.platform, a.status, a.username) for a in accounts], [
            ("TIKTOK", AccountStatus.CONNECTED, "@estudionorte"),
            ("INSTAGRAM_REELS", AccountStatus.EXPIRED, "@estudio.norte"),
            ("YOUTUBE_SHORTS", AccountStatus.DISCONNECTED, None),
        ])
        self.assertEqual(accounts[0].connected_at, datetime(2026, 11, 2, 2, 11, 53, 452000, tzinfo=dt_timezone.utc))

    def test_youtube_shows_its_handle(self):
        body = {"activeSocialAccounts": ["youtube"], "displayNames": [
            {"platform": "youtube", "username": "Kenneth Chen", "handle": "@ayrshare", "channelTitle": "Ayrshare"},
        ]}
        with patch(REQUEST, return_value=answer(200, body)):
            youtube = AyrshareProvider().accounts(PROFILE)[2]
        self.assertEqual((youtube.status, youtube.username), (AccountStatus.CONNECTED, "@ayrshare"))

    def test_disconnect_and_delete(self):
        with patch(REQUEST, return_value=answer(200, {"status": "success"})) as sent:
            AyrshareProvider().disconnect(PROFILE, "YOUTUBE_SHORTS")
        self.assertEqual(sent.call_args.args, ("delete", "https://api.ayrshare.com/api/profiles/social"))
        self.assertEqual(sent.call_args.kwargs["json"], {"platform": "youtube"})
        self.assertEqual(sent.call_args.kwargs["headers"]["Profile-Key"], "PROFILE-KEY-1")

        gone = {"action": "post", "status": "error", "code": 144, "message": "Some profiles not found."}
        with patch(REQUEST, return_value=answer(403, gone)) as sent:
            AyrshareProvider().delete_profile(PROFILE)  # already gone: not an error
        self.assertEqual(sent.call_args.args, ("delete", "https://api.ayrshare.com/api/profiles"))
        with patch(REQUEST, return_value=answer(500, None)), self.assertRaises(RetryableError):
            AyrshareProvider().delete_profile(PROFILE)
