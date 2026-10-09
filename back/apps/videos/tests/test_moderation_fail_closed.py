"""AI_Security_Shield must fail closed: no verdict from moderation never means "safe"."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.security import (
    AI_Security_Shield,
    ModerationBlockedError,
    ModerationError,
    ModerationUnavailableError,
    UnsafeContentError,
)
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tasks import process_video_seo
from apps.videos.tests.fakes import CapturedTaskFailures

User = get_user_model()

OPENAI_CLIENT = "apps.core.security.OpenAI"
SECRET_TEXT = "my-private-transcript-xyz"
PROVIDER_BODY = "provider-said-secret-detail"


def _response(status, code=None):
    body = {"error": {"message": PROVIDER_BODY, "type": code, "code": code}}
    return httpx.Response(
        status, request=httpx.Request("POST", "https://api.openai.test/v1/moderations"), json=body
    )


def status_error(cls, status, code=None):
    body = {"message": PROVIDER_BODY, "type": code, "code": code}
    return cls(PROVIDER_BODY, response=_response(status, code), body=body)


def verdict(flagged):
    result = SimpleNamespace(flagged=flagged, categories=SimpleNamespace(hate=flagged))
    return SimpleNamespace(results=[result])


def client_raising(exc):
    client = MagicMock()
    client.moderations.create.side_effect = exc
    return client


@override_settings(OPENAI_API_KEY="test-key", MODERATION_TIMEOUT_SECONDS=7)
class CheckContentSafetyTests(SimpleTestCase):
    def test_clean_content_is_allowed(self):
        client = MagicMock()
        client.moderations.create.return_value = verdict(False)
        with patch(OPENAI_CLIENT, return_value=client):
            self.assertTrue(AI_Security_Shield.check_content_safety("hello"))

    def test_flagged_content_raises_unsafe(self):
        client = MagicMock()
        client.moderations.create.return_value = verdict(True)
        with patch(OPENAI_CLIENT, return_value=client):
            with self.assertRaises(UnsafeContentError):
                AI_Security_Shield.check_content_safety("bad")

    def test_empty_payload_is_allowed_without_calling_the_api(self):
        with patch(OPENAI_CLIENT) as factory:
            self.assertTrue(AI_Security_Shield.check_content_safety(""))
        factory.assert_not_called()

    def test_client_is_built_with_timeout_and_no_sdk_retries(self):
        client = MagicMock()
        client.moderations.create.return_value = verdict(False)
        with patch(OPENAI_CLIENT, return_value=client) as factory:
            AI_Security_Shield.check_content_safety("hello")
        factory.assert_called_once_with(api_key="test-key", timeout=7.0, max_retries=0)

    def test_default_timeout_setting_is_ten_seconds(self):
        from backend.settings import base

        self.assertEqual(base.MODERATION_TIMEOUT_SECONDS, 10.0)

    def test_missing_api_key_is_non_retryable_and_skips_the_api(self):
        for key in (None, ""):
            with self.subTest(key=key), override_settings(OPENAI_API_KEY=key):
                with patch(OPENAI_CLIENT) as factory:
                    with self.assertRaises(ModerationBlockedError) as ctx:
                        AI_Security_Shield.check_content_safety("hello")
                factory.assert_not_called()
                self.assertFalse(ctx.exception.retryable)
                self.assertEqual(ctx.exception.code, "moderation_misconfigured")

    def test_failures_are_classified_and_never_allowed(self):
        req = httpx.Request("POST", "https://api.openai.test/v1/moderations")
        cases = [
            ("timeout", openai.APITimeoutError(request=req), True, "moderation_unavailable"),
            ("connection", openai.APIConnectionError(request=req), True, "moderation_unavailable"),
            ("500", status_error(openai.InternalServerError, 500), True, "moderation_unavailable"),
            ("503", status_error(openai.InternalServerError, 503), True, "moderation_unavailable"),
            ("429", status_error(openai.RateLimitError, 429, "rate_limit_exceeded"), True, "moderation_unavailable"),
            ("429-nocode", status_error(openai.RateLimitError, 429), True, "moderation_unavailable"),
            ("quota", status_error(openai.RateLimitError, 429, "insufficient_quota"), False, "moderation_quota"),
            ("401", status_error(openai.AuthenticationError, 401, "invalid_api_key"), False, "moderation_misconfigured"),
            ("403", status_error(openai.PermissionDeniedError, 403), False, "moderation_misconfigured"),
            ("400", status_error(openai.BadRequestError, 400), False, "moderation_misconfigured"),
            ("404", status_error(openai.NotFoundError, 404), False, "moderation_misconfigured"),
            ("unknown", RuntimeError(PROVIDER_BODY), True, "moderation_unavailable"),
            ("bad-shape", KeyError("results"), True, "moderation_unavailable"),
        ]
        for name, exc, retryable, code in cases:
            with self.subTest(name), patch(OPENAI_CLIENT, return_value=client_raising(exc)):
                with self.assertRaises(ModerationError) as ctx:
                    AI_Security_Shield.check_content_safety("hello")
                self.assertEqual(ctx.exception.retryable, retryable)
                self.assertEqual(ctx.exception.code, code)
                self.assertNotIn(PROVIDER_BODY, str(ctx.exception))
                self.assertIsInstance(
                    ctx.exception,
                    ModerationUnavailableError if retryable else ModerationBlockedError,
                )

    def test_logs_never_contain_payload_or_provider_message(self):
        exc = status_error(openai.RateLimitError, 429, "insufficient_quota")
        with patch(OPENAI_CLIENT, return_value=client_raising(exc)):
            with self.assertLogs("apps.core.security", level="DEBUG") as logs:
                with self.assertRaises(ModerationError):
                    AI_Security_Shield.check_content_safety(SECRET_TEXT)
        output = "\n".join(logs.output)
        self.assertNotIn(SECRET_TEXT, output)
        self.assertNotIn(PROVIDER_BODY, output)
        self.assertIn("RateLimitError", output)


class ProcessVideoSeoModerationTests(CapturedTaskFailures, TestCase):
    CHECK = "apps.core.security.AI_Security_Shield.check_content_safety"

    def setUp(self):
        user = User.objects.create_user(username="seo", email="seo@test.com", password="pw")
        workspace = Workspace.objects.create(name="SEO", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="S", metadata={"full_text": SECRET_TEXT}
        )
        clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)
        self.post = ScheduledPost.objects.create(
            video_clip=clip,
            platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z",
            status=ScheduledPost.Status.QUEUED,
        )

    def run_task(self, retries=0):
        process_video_seo.push_request(retries=retries)
        try:
            return process_video_seo.run(str(self.post.id))
        finally:
            process_video_seo.pop_request()

    def test_retryable_error_retries_with_exponential_backoff_and_jitter(self):
        for retries, low, high in ((0, 15, 30), (1, 30, 60), (2, 60, 120)):
            with self.subTest(retries=retries):
                with patch(self.CHECK, side_effect=ModerationUnavailableError()), patch.object(
                    process_video_seo, "retry", side_effect=Retry()
                ) as retry:
                    with self.assertRaises(Retry):
                        self.run_task(retries)
                countdown = retry.call_args.kwargs["countdown"]
                self.assertGreaterEqual(countdown, low)
                self.assertLessEqual(countdown, high)
                self.post.refresh_from_db()
                self.assertNotEqual(self.post.status, ScheduledPost.Status.FAILED)

    def test_retries_exhausted_marks_post_failed_with_static_code(self):
        with patch(self.CHECK, side_effect=ModerationUnavailableError()), patch.object(
            process_video_seo, "retry"
        ) as retry:
            self.run_task(retries=process_video_seo.max_retries)
        retry.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "moderation_unavailable")

    def test_non_retryable_errors_fail_immediately_without_retry(self):
        for exc, code in (
            (ModerationBlockedError(code="moderation_misconfigured"), "moderation_misconfigured"),
            (ModerationBlockedError(code="moderation_quota"), "moderation_quota"),
        ):
            with self.subTest(code=code):
                ScheduledPost.objects.filter(pk=self.post.pk).update(
                    status=ScheduledPost.Status.QUEUED, error_log=None
                )
                with patch(self.CHECK, side_effect=exc), patch.object(
                    process_video_seo, "retry"
                ) as retry:
                    self.run_task(retries=0)
                retry.assert_not_called()
                self.post.refresh_from_db()
                self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
                self.assertEqual(self.post.error_log, code)

    def test_unsafe_content_path_is_unchanged(self):
        with patch(self.CHECK, side_effect=UnsafeContentError("flagged")), patch.object(
            process_video_seo, "retry"
        ) as retry:
            result = self.run_task()
        retry.assert_not_called()
        self.assertEqual(result, "Security Abort")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertTrue(self.post.error_log.startswith("Violación de seguridad"))

    def test_generic_handler_never_receives_moderation_errors(self):
        # A fixed countdown=60 retry would be the blind retry of the old generic handler.
        with patch(self.CHECK, side_effect=ModerationUnavailableError()), patch.object(
            process_video_seo, "retry", side_effect=Retry()
        ) as retry:
            with self.assertRaises(Retry):
                self.run_task(retries=0)
        self.assertNotEqual(retry.call_args.kwargs.get("countdown"), 60)
        self.assertNotIn("exc", retry.call_args.kwargs)

    def test_task_logs_do_not_leak_transcript(self):
        with patch(self.CHECK, side_effect=ModerationBlockedError(code="moderation_quota")):
            with self.assertLogs("apps.videos.tasks", level="DEBUG") as logs:
                self.run_task()
        self.assertNotIn(SECRET_TEXT, "\n".join(logs.output))
