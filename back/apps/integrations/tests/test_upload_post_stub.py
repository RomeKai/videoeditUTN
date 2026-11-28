"""
End to end against a local stand-in of Upload-Post (upload_post_stub.py, the
same one a rehearsal runs), over real HTTP: a workspace created with
SOCIAL_PROVIDER=upload_post connects a network, the API shows its TikTok
options, and the publisher (upload_to_social_network) publishes through it,
waits for an upload that goes on in the background, and never sends one twice.
"""
from io import StringIO
from unittest.mock import patch

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.integrations.models import SocialProfile
from apps.integrations.social import AccountStatus, PublishRequest, RetryableError, TikTokOptions, upload_post
from apps.integrations.social import accounts as social_accounts
from apps.integrations.social.upload_post import UploadPostProvider
from apps.integrations.tests.upload_post_stub import UploadPostStub
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tasks import upload_to_social_network

User = get_user_model()
PRESIGN = "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url"
MEDIA = "https://r2.test/clips/a.mp4?X-Amz-Signature=abc"
TIKTOK = {"privacy_level": "FOLLOWER_OF_CREATOR", "allow_comment": True, "allow_duet": False,
          "allow_stitch": True, "brand_organic": False, "branded_content": False}


@pytest.fixture
def stub(network_guard, monkeypatch, settings):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = UploadPostStub().start()
    monkeypatch.setattr(network_guard, "allowed_ports", frozenset({*network_guard.allowed_ports, server.port}))
    settings.UPLOAD_POST_BASE_URL = server.base_url
    settings.UPLOAD_POST_API_KEY = "stub-key"
    settings.SOCIAL_PROVIDER = "upload_post"
    settings.SOCIAL_PROFILE_TITLE_PREFIX = "oc-stub"
    settings.FRONTEND_URL = "https://app.test"
    # The checks of a background upload, quicker: the same loop, the same deadline rule.
    monkeypatch.setattr(upload_post, "STATUS_POLL_SECONDS", 0.02)
    yield server
    server.stop()


def control(server, **modes):
    requests.get(f"{server.base_url}/ctl", params=modes, timeout=5).raise_for_status()


@pytest.fixture
def workspace(db):
    user = User.objects.create_user(username="ana", email="ana@test.com", password="pw", is_email_verified=True)
    return user.owned_workspaces.get()


def linked(server, workspace, platform="TIKTOK"):
    """The workspace's profile at the stand-in, with ``platform`` linked through its page."""
    url = social_accounts.connect_url(workspace, platform, None)
    requests.get(url, timeout=5).raise_for_status()
    return SocialProfile.objects.get(workspace=workspace)


def test_connect_read_and_disconnect_through_the_stand_in(stub, workspace):
    control(stub, privacy="FOLLOWER_OF_CREATOR,SELF_ONLY")
    url = social_accounts.connect_url(workspace, "TIKTOK", "https://app.test/ajustes/redes?conectada=1")

    profile = SocialProfile.objects.get(workspace=workspace)
    # A new profile takes SOCIAL_PROVIDER; its ref is the username at Upload-Post.
    assert (profile.provider, profile.provider_ref) == ("upload_post", f"oc-stub-{workspace.pk}")
    assert stub.state.links == [{"platforms": ["tiktok"], "redirect_url": "https://app.test/ajustes/redes?conectada=1",
                                 "language": "es", "show_calendar": False}]
    requests.get(url, timeout=5).raise_for_status()  # the user authorizes TikTok on the page

    found = social_accounts.accounts(workspace, fresh=True)
    assert [(a.platform, a.status, a.username) for a in found] == [
        ("TIKTOK", AccountStatus.CONNECTED, "@ana.demo"),
        ("INSTAGRAM_REELS", AccountStatus.DISCONNECTED, None),
        ("YOUTUBE_SHORTS", AccountStatus.DISCONNECTED, None),
    ]
    profile.refresh_from_db()
    assert profile.networks["TIKTOK"] == "connected" and profile.linked_at is not None

    # The API gives the panel this account's own TikTok options (from the cache it just filled).
    client = APIClient()
    client.force_authenticate(workspace.owner)
    page = client.get("/api/v1/social-accounts/", HTTP_X_WORKSPACE_ID=str(workspace.pk))
    assert page.status_code == 200, page.content
    assert page.json()[0]["tiktok"] == {
        "privacy_options": ["FOLLOWER_OF_CREATOR", "SELF_ONLY"],
        "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False,
    }

    social_accounts.disconnect(workspace, "TIKTOK")
    assert stub.state.profiles[profile.provider_ref] == {}
    # Connecting again reuses the profile: Upload-Post answers 409 for a username it has, never a second one.
    social_accounts.connect_url(workspace, "INSTAGRAM_REELS", None)
    assert [path for method, path in stub.state.calls if method == "POST" and path == "/api/uploadposts/users"] == [
        "/api/uploadposts/users",
    ]


def test_the_smoke_command_against_the_stand_in(stub, workspace):
    out = StringIO()
    call_command("social_smoke", "--workspace", str(workspace.pk), "--ignore-plan", stdout=out)
    output = out.getvalue()

    assert "Perfil creado en el proveedor." in output
    assert f"proveedor: upload_post · id: oc-stub-{workspace.pk}" in output
    assert "Link para conectar TIKTOK (vale 48 horas):" in output
    assert "TIKTOK: disconnected" in output
    assert "stub-key" not in output


def test_an_existing_ayrshare_profile_keeps_its_provider(stub, workspace):
    SocialProfile.objects.create(workspace=workspace, provider="ayrshare", provider_ref="PROFILE-KEY")
    with patch("apps.integrations.social.ayrshare.AyrshareProvider.connect_url", return_value="https://ayr.test/x") as ayr:
        assert social_accounts.connect_url(workspace, "TIKTOK", None) == "https://ayr.test/x"
    ayr.assert_called_once()
    assert stub.state.calls == []


def test_the_same_key_twice_is_the_same_job(stub, workspace):
    """#186/#192: a worker's retry sends the same key, and Upload-Post answers the original job."""
    profile = linked(stub, workspace)
    request = PublishRequest(platform="TIKTOK", media_url=MEDIA, text="Hola", title="Clip",
                             idempotency_key="onecreator-post-1-v0", tiktok=TikTokOptions("SELF_ONLY"))
    first = UploadPostProvider().publish(profile, request)
    again = UploadPostProvider().publish(profile, request)
    assert first == again
    assert [upload["idempotency_key"] for upload in stub.state.uploads] == ["onecreator-post-1-v0"] * 2
    assert len(stub.state.replies) == 1  # one job


class Publisher:
    def __init__(self, workspace):
        self.project = VideoProject.objects.create(workspace=workspace, uploaded_by=workspace.owner, title="Entrevista")
        self.clip = VideoClip.objects.create(
            project=self.project, title="La remontada", start_time=0, end_time=30,
            status=VideoClip.Status.COMPLETED, s3_object_key="clips/a.mp4",
        )

    def post(self, platform="TIKTOK", **fields):
        if platform == "TIKTOK":
            fields.setdefault("tiktok", TIKTOK)
        return ScheduledPost.objects.create(
            video_clip=self.clip, platform=platform, status=ScheduledPost.Status.QUEUED,
            publish_at="2030-01-01T00:00:00Z", caption="Mirá esto", hashtags=["gol"], **fields,
        )

    def run(self, post):
        with patch(PRESIGN, return_value=MEDIA):
            result = upload_to_social_network.apply(args=[str(post.pk)])
        post.refresh_from_db()
        return result


@pytest.fixture
def publisher(stub, workspace, settings):
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    linked(stub, workspace)
    return Publisher(workspace)


def test_the_worker_publishes_through_the_stand_in(stub, publisher):
    post = publisher.post()
    publisher.run(post)

    assert post.status == ScheduledPost.Status.PUBLISHED, post.error_log
    assert post.post_url.startswith("https://www.tiktok.com/@ana.demo/video/")
    key = f"onecreator-post-{post.pk}-v0"
    assert post.external_id == key  # Upload-Post kept our request_id
    (upload,) = stub.state.uploads
    fields = dict(upload["fields"])
    assert upload["idempotency_key"] == key
    assert {name: fields[name] for name in (
        "user", "platform[]", "video", "request_id", "tiktok_title", "privacy_level",
        "disable_comment", "disable_duet", "disable_stitch",
    )} == {
        "user": publisher.project.workspace.social_profile.provider_ref, "platform[]": "tiktok", "video": MEDIA,
        "request_id": key, "tiktok_title": "Mirá esto\n\n#gol", "privacy_level": "FOLLOWER_OF_CREATOR",
        "disable_comment": "false", "disable_duet": "true", "disable_stitch": "false",
    }


def test_the_worker_waits_for_a_background_upload(stub, publisher):
    control(stub, upload="async", status_after=3)
    post = publisher.post()
    publisher.run(post)

    assert post.status == ScheduledPost.Status.PUBLISHED, post.error_log
    assert post.post_url.startswith("https://www.tiktok.com/")
    reads = [path for method, path in stub.state.calls if path == "/api/uploadposts/status"]
    assert len(reads) == 3  # two "Queued" answers with success: true, then completed


def test_an_upload_that_never_finishes_is_unknown_and_never_sent_again(stub, publisher, monkeypatch):
    control(stub, upload="pending")
    monkeypatch.setattr(upload_post, "PUBLISH_DEADLINE_SECONDS", 0.5)
    post = publisher.post()
    result = publisher.run(post)

    assert result.failed()
    assert post.status == ScheduledPost.Status.FAILED
    assert post.error_log.startswith("outcome_unknown (upload-post async; status=in_progress; request_id=onecreator-post-")
    assert post.post_url is None
    assert len(stub.state.uploads) == 1  # the worker never retries it


@pytest.mark.parametrize("mode", ["gateway504", "drop"])
def test_an_answer_lost_after_sending_is_settled_by_its_status(stub, publisher, mode):
    """A 504, or a connection closed after the request: the upload went on, and its status says so."""
    control(stub, upload=mode, status_after=1)
    post = publisher.post()
    publisher.run(post)

    assert post.status == ScheduledPost.Status.PUBLISHED, post.error_log
    assert len(stub.state.uploads) == 1


def test_a_networks_failure_is_our_code(stub, publisher):
    control(stub, upload="fail:account_reauth_required")
    post = publisher.post()
    publisher.run(post)

    assert post.status == ScheduledPost.Status.FAILED
    assert post.error_log == "account_expired (upload-post code=account_reauth_required)"


def test_a_platform_that_failed_or_timed_out_is_one_job_and_unknown(stub, publisher):
    """
    Review of #199: `platform_unavailable` («the platform failed or timed out») may be out. Before, each
    retry took a new key: four jobs for one post, then «Reintentar» a fifth. Now one job, unknown, and the
    key stays marked unclear.
    """
    control(stub, upload="fail:platform_unavailable", status_after=1)
    post = publisher.post()
    result = publisher.run(post)

    assert result.failed()
    assert post.status == ScheduledPost.Status.FAILED
    assert post.error_log == "outcome_unknown (upload-post code=platform_unavailable)"
    assert post.publish_key_unclear is True
    assert [upload["idempotency_key"] for upload in stub.state.uploads] == [f"onecreator-post-{post.pk}-v0"]


def test_a_refusal_upload_post_retries_by_itself_is_published_once(stub, publisher):
    control(stub, upload="retrying:platform_rate_limited", status_after=2)
    post = publisher.post()
    publisher.run(post)

    assert post.status == ScheduledPost.Status.PUBLISHED, post.error_log
    assert len(stub.state.uploads) == 1


def test_a_retry_later_refusal_spends_its_key_only_once_failed(stub, workspace):
    profile = linked(stub, workspace)
    control(stub, upload="fail:platform_rate_limited", status_after=2)
    request = PublishRequest(platform="TIKTOK", media_url=MEDIA, text="Hola", title="Clip",
                             idempotency_key="onecreator-post-9-v0", tiktok=TikTokOptions("SELF_ONLY"))
    with pytest.raises(RetryableError) as caught:
        UploadPostProvider().publish(profile, request)
    assert (caught.value.key_spent, caught.value.maybe_sent) == (True, False)
    # It waited for the status to say `failed` (the first read said `retryable`).
    assert [path for _, path in stub.state.calls].count("/api/uploadposts/status") == 2


def test_a_network_that_is_not_linked_sends_nothing_out(stub, publisher):
    post = publisher.post("YOUTUBE_SHORTS")
    publisher.run(post)

    assert post.status == ScheduledPost.Status.FAILED
    assert post.error_log == "account_not_connected (upload-post http=400)"
