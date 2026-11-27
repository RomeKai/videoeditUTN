"""
Gap 10: the networks of a workspace (/api/v1/social-accounts/, contract
`social.accounts`, `social.connect`, `social.disconnect`), with the provider
replaced (no network). Its profile is created the first time a network is
connected, once even if two connects arrive together; the API checks each
network with what the provider said last; deleting the workspace deletes the
profile at the provider afterwards.
"""
import io
import threading
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.integrations.models import SocialProfile
from apps.integrations.social import (
    Account,
    AccountStatus,
    CreatedProfile,
    PermanentError,
    RetryableError,
)
from apps.integrations.tasks import delete_orphan_social_profiles
from apps.integrations.tests.helpers import give_social_profile
from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import ScheduledPost, VideoClip, VideoProject

User = get_user_model()
Status, Platform = ScheduledPost.Status, ScheduledPost.Platform
PROVIDER = "apps.integrations.social.ayrshare.AyrshareProvider"
LIST = reverse("social-account-list")
CONNECT = reverse("social-account-connect")
LINK = "https://profile.ayrshare.com?session=ayr_ls_x"
CONNECTED_AT = datetime(2026, 11, 2, 2, 11, 53, tzinfo=dt_timezone.utc)


def detail(platform):
    return reverse("social-account-detail", args=[platform])


def linked(**statuses):
    """What the provider answers: every network disconnected but the ones given."""
    names = {"TIKTOK": "@estudionorte", "INSTAGRAM_REELS": "@estudio.norte", "YOUTUBE_SHORTS": "@estudionorte"}
    return [
        Account(platform, statuses.get(platform, AccountStatus.DISCONNECTED),
                names[platform] if platform in statuses else None, CONNECTED_AT if platform in statuses else None)
        for platform in ("TIKTOK", "INSTAGRAM_REELS", "YOUTUBE_SHORTS")
    ]


class SocialAccountsHarness:
    def setUp(self):
        super().setUp()
        overridden = override_settings(FRONTEND_URL="https://app.onecreator.test", SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
        overridden.enable()
        self.addCleanup(overridden.disable)
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = User.objects.create_user(username="duena", email="duena@test.com", password="pw")
        self.workspace = self.owner.owned_workspaces.get()
        # A plan without the limits these tests are not about (members, crossposting).
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=SubscriptionPlan.objects.create(
            name="Redes de prueba", max_members=10, allow_scheduling=True, allow_crossposting=True,
            max_video_duration_seconds=3600,
        ))
        self.workspace.refresh_from_db()
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def call(self, method, url, body=None, user=None, workspace=None):
        api = self.api
        if user is not None:
            api = APIClient()
            api.force_authenticate(user)
        return getattr(api, method)(url, body or {}, format="json",
                                    HTTP_X_WORKSPACE_ID=str((workspace or self.workspace).pk))

    def member(self, role):
        user = User.objects.create_user(username=f"m-{role}", email=f"{role}@test.com", password="pw")
        WorkspaceMember.objects.create(workspace=self.workspace, user=user, role=role, status=WorkspaceMember.Status.ACTIVE)
        return user

    def profile(self):
        return SocialProfile.objects.filter(workspace=self.workspace).first()


class ListTests(SocialAccountsHarness, TestCase):
    def test_without_a_profile_every_network_is_disconnected_and_nobody_is_asked(self):
        with patch(f"{PROVIDER}.accounts") as accounts:
            response = self.call("get", LIST)
        accounts.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([(row["platform"], row["status"], row["username"]) for row in body], [
            ("TIKTOK", "disconnected", None), ("INSTAGRAM_REELS", "disconnected", None), ("YOUTUBE_SHORTS", "disconnected", None),
        ])
        self.assertEqual(body[2]["limits"], {
            "caption_max_chars": 5000, "hashtags_max": 15, "max_duration_seconds": 180, "aspect_ratios": ["9:16"],
        })
        self.assertIsNone(body[0]["tiktok"])

    def test_the_provider_says_which_are_connected(self):
        give_social_profile(self.workspace, "PROFILE-KEY-SECRET")
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected", INSTAGRAM_REELS="expired")) as accounts:
            response = self.call("get", LIST, user=self.member(WorkspaceMember.Role.VIEWER))  # any member reads
        self.assertEqual(response.status_code, 200)
        accounts.assert_called_once()
        tiktok, instagram, youtube = response.json()
        self.assertEqual((tiktok["status"], tiktok["username"], tiktok["connected_at"]),
                         ("connected", "@estudionorte", "2026-11-02T02:11:53Z"))
        self.assertEqual(tiktok["tiktok"], {
            "privacy_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
            "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False,
        })
        self.assertEqual(tiktok["limits"]["max_duration_seconds"], 600)
        self.assertEqual((instagram["status"], instagram["limits"]["max_duration_seconds"]), ("expired", 900))
        self.assertEqual((youtube["status"], youtube["username"], youtube["tiktok"]), ("disconnected", None, None))
        self.assertNotIn("PROFILE-KEY-SECRET", response.content.decode())
        # What the API checks without asking the provider.
        self.assertEqual(self.profile().networks, {
            "TIKTOK": "connected", "INSTAGRAM_REELS": "expired", "YOUTUBE_SHORTS": "disconnected",
        })

    def test_the_provider_is_asked_once_in_a_while(self):
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")) as accounts:
            for _ in range(3):
                self.assertEqual(self.call("get", LIST).status_code, 200)
        accounts.assert_called_once()

    def test_a_provider_failure_is_a_503_with_a_code(self):
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", side_effect=RetryableError(detail="ayrshare http=502")):
            response = self.call("get", LIST)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "provider_unavailable")

    def test_a_profile_the_provider_lost_is_forgotten(self):
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", side_effect=PermanentError("account_not_connected", "ayrshare http=403 code=144")):
            response = self.call("get", LIST)
        self.assertEqual({row["status"] for row in response.json()}, {"disconnected"})
        self.assertEqual(self.profile().provider_ref, "")


class ConnectTests(SocialAccountsHarness, TestCase):
    def test_only_owner_and_admins_connect(self):
        for role in (WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER):
            with self.subTest(role=role), patch(f"{PROVIDER}.create_profile") as create:
                response = self.call("post", CONNECT, {"platform": "TIKTOK"}, user=self.member(role))
                self.assertEqual(response.status_code, 403)
                create.assert_not_called()
        admin = self.member(WorkspaceMember.Role.ADMIN)
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("KEY", "ref-1")), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            self.assertEqual(self.call("post", CONNECT, {"platform": "TIKTOK"}, user=admin).status_code, 200)

    def test_the_first_connect_creates_the_profile_and_the_next_ones_reuse_it(self):
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("KEY-1", "ref-1")) as create, \
                patch(f"{PROVIDER}.connect_url", return_value=LINK) as link:
            first = self.call("post", CONNECT, {"platform": "TIKTOK", "return_url": "https://app.onecreator.test/ajustes/redes"})
            second = self.call("post", CONNECT, {"platform": "YOUTUBE_SHORTS"})
        self.assertEqual((first.status_code, first.json()), (200, {"connect_url": LINK}))
        self.assertEqual(second.status_code, 200)
        create.assert_called_once()
        profile = self.profile()
        self.assertEqual((profile.provider, profile.provider_ref, profile.provider_id, profile.networks),
                         ("ayrshare", "KEY-1", "ref-1", {}))
        self.assertEqual(link.call_args_list[0].args[1:], ("TIKTOK", "https://app.onecreator.test/ajustes/redes"))
        # Without return_url, none: the provider's «Close» closes the window the web app opened.
        self.assertEqual(link.call_args_list[1].args[1:], ("YOUTUBE_SHORTS", None))
        # A worker of the release before SocialProfile still finds the key in the old column.
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.ayrshare_profile_key, "KEY-1")
        self.assertNotIn("KEY-1", first.content.decode())

    def test_return_url_must_be_the_web_app(self):
        for url in ("https://evil.test/ajustes", "http://app.onecreator.test/ajustes", "https://app.onecreator.test.evil.test/",
                    "https://user@evil.test/", "https://app.onecreator.test@evil.test/", "javascript:alert(1)"):
            with self.subTest(url=url), patch(f"{PROVIDER}.create_profile") as create:
                response = self.call("post", CONNECT, {"platform": "TIKTOK", "return_url": url})
                self.assertEqual(response.status_code, 400)
                self.assertIn("return_url", response.json())
                create.assert_not_called()

    def test_a_platform_that_is_not_ours_is_a_400(self):
        response = self.call("post", CONNECT, {"platform": "FACEBOOK"})
        self.assertEqual(response.json(), {"platform": ["Elegí TikTok, Instagram Reels o YouTube Shorts."]})

    def test_a_provider_failure_creates_nothing_usable(self):
        with patch(f"{PROVIDER}.create_profile", side_effect=RetryableError(detail="ayrshare ConnectTimeout")):
            response = self.call("post", CONNECT, {"platform": "TIKTOK"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "error": "No pudimos hablar con TikTok. Probá de nuevo en unos minutos.", "code": "provider_unavailable",
        })
        self.assertEqual(self.profile().provider_ref, "")
        # The next one tries again.
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("KEY-2")), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            self.assertEqual(self.call("post", CONNECT, {"platform": "TIKTOK"}).status_code, 200)
        self.assertEqual(self.profile().provider_ref, "KEY-2")

    def test_a_profile_the_provider_lost_is_made_again(self):
        give_social_profile(self.workspace, "OLD")
        lost = PermanentError("account_not_connected", "ayrshare http=403 code=144")
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("NEW")) as create, \
                patch(f"{PROVIDER}.connect_url", side_effect=[lost, LINK]):
            response = self.call("post", CONNECT, {"platform": "INSTAGRAM_REELS"})
        self.assertEqual(response.status_code, 200)
        create.assert_called_once()
        self.assertEqual(self.profile().provider_ref, "NEW")

    def test_connecting_forgets_the_cached_accounts(self):
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", side_effect=[linked(), linked(TIKTOK="connected")]), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "disconnected")
            self.call("post", CONNECT, {"platform": "TIKTOK"})
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "connected")


class DisconnectTests(SocialAccountsHarness, TestCase):
    def setUp(self):
        super().setUp()
        give_social_profile(self.workspace)
        SocialProfile.objects.filter(workspace=self.workspace).update(
            networks={"TIKTOK": "connected", "INSTAGRAM_REELS": "connected", "YOUTUBE_SHORTS": "disconnected"},
        )
        project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.owner, title="P")
        self.clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5,
                                             status=VideoClip.Status.COMPLETED, s3_object_key="c.mp4")

    def post_in(self, platform, status):
        return ScheduledPost.objects.create(video_clip=self.clip, platform=platform, status=status,
                                            publish_at="2030-01-01T00:00:00Z", tiktok={"privacy_level": "SELF_ONLY"})

    def test_editors_cannot_and_unknown_networks_are_404(self):
        with patch(f"{PROVIDER}.disconnect") as unlink:
            self.assertEqual(self.call("delete", detail("TIKTOK"), user=self.member(WorkspaceMember.Role.EDITOR)).status_code, 403)
            self.assertEqual(self.call("delete", detail("FACEBOOK")).status_code, 404)
        unlink.assert_not_called()

    def test_the_posts_going_out_on_that_network_fail(self):
        going = [self.post_in("TIKTOK", Status.SCHEDULED), self.post_in("TIKTOK", Status.QUEUED)]
        kept = {
            self.post_in("TIKTOK", Status.DRAFT).pk: Status.DRAFT,
            self.post_in("TIKTOK", Status.PROCESSING).pk: Status.PROCESSING,
            self.post_in("TIKTOK", Status.PUBLISHED).pk: Status.PUBLISHED,
            self.post_in("INSTAGRAM_REELS", Status.SCHEDULED).pk: Status.SCHEDULED,
        }
        with patch(f"{PROVIDER}.disconnect") as unlink:
            response = self.call("delete", detail("TIKTOK"))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(unlink.call_args.args[1], "TIKTOK")
        for post in going:
            post.refresh_from_db()
            self.assertEqual((post.status, post.error_log), (Status.FAILED, "account_not_connected (disconnected)"))
        self.assertEqual(dict(ScheduledPost.objects.filter(pk__in=kept).values_list("pk", "status")), kept)
        self.assertEqual(self.profile().networks["TIKTOK"], "disconnected")
        # The API translates it, and the next post on TikTok is refused until it is connected again.
        row = self.call("get", reverse("scheduled-post-detail", args=[going[0].pk])).json()
        self.assertEqual((row["error_code"], row["error_message"]),
                         ("account_not_connected", "La cuenta de TikTok no está conectada. Conectala y reintentá."))

    def test_a_provider_failure_changes_nothing(self):
        post = self.post_in("TIKTOK", Status.SCHEDULED)
        with patch(f"{PROVIDER}.disconnect", side_effect=RetryableError(detail="ayrshare http=503")):
            response = self.call("delete", detail("TIKTOK"))
        self.assertEqual(response.status_code, 503)
        post.refresh_from_db()
        self.assertEqual(post.status, Status.SCHEDULED)
        self.assertEqual(self.profile().networks["TIKTOK"], "connected")


class AccountCheckTests(SocialAccountsHarness, TestCase):
    """Publishing checks the network with the provider's last read, without calling it."""

    def setUp(self):
        super().setUp()
        project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.owner, title="P")
        self.clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5,
                                             status=VideoClip.Status.COMPLETED, s3_object_key="c.mp4")
        self.create = reverse("scheduled-post-list")

    def publish(self, platform):
        body = {"video_clip": str(self.clip.pk), "platform": platform, "tiktok": {"privacy_level": "SELF_ONLY"}}
        with patch("apps.videos.tasks.process_video_seo.delay"), patch(f"{PROVIDER}.accounts") as asked:
            response = self.call("post", self.create, body)
        asked.assert_not_called()
        return response

    def test_each_network_on_its_own(self):
        give_social_profile(self.workspace)
        SocialProfile.objects.filter(workspace=self.workspace).update(
            networks={"TIKTOK": "connected", "INSTAGRAM_REELS": "expired", "YOUTUBE_SHORTS": "disconnected"},
        )
        self.assertEqual(self.publish("TIKTOK").status_code, 201)
        expired = self.publish("INSTAGRAM_REELS")
        self.assertEqual((expired.status_code, expired.json()), (409, {
            "error": "La cuenta de Instagram Reels está vencida. Reconectala para publicar.", "code": "account_not_connected",
        }))
        self.assertEqual(self.publish("YOUTUBE_SHORTS").json()["error"],
                         "La cuenta de YouTube Shorts no está conectada. Conectala para publicar.")

    def test_a_profile_never_read_publishes_as_before(self):
        give_social_profile(self.workspace)  # copied from ayrshare_profile_key: networks unknown
        self.assertEqual(self.publish("YOUTUBE_SHORTS").status_code, 201)

    def test_the_onboarding_step_counts_connected_networks(self):
        me = "/api/v1/me/"
        self.assertFalse(self.call("get", me).json()["onboarding"]["has_social_account"])
        give_social_profile(self.workspace)
        SocialProfile.objects.filter(workspace=self.workspace).update(networks={})
        self.assertFalse(self.call("get", me).json()["onboarding"]["has_social_account"])  # created, nothing linked
        SocialProfile.objects.filter(workspace=self.workspace).update(networks={"TIKTOK": "expired"})
        self.assertFalse(self.call("get", me).json()["onboarding"]["has_social_account"])
        SocialProfile.objects.filter(workspace=self.workspace).update(networks={"TIKTOK": "connected"})
        self.assertTrue(self.call("get", me).json()["onboarding"]["has_social_account"])


class WorkspaceDeleteTests(SocialAccountsHarness, TestCase):
    def setUp(self):
        super().setUp()
        Workspace.objects.create(name="Otro", owner=self.owner)  # the last workspace is never deleted

    def delete(self):
        url = reverse("workspace-detail", args=[self.workspace.pk])
        return self.api.delete(url, {"confirm_name": self.workspace.name}, format="json")

    def test_the_profile_is_deleted_at_the_provider_after_the_commit(self):
        give_social_profile(self.workspace, "KEY-GONE")
        profile_id = self.profile().pk
        with patch(f"{PROVIDER}.delete_profile") as delete_profile:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.delete()
            self.assertEqual(response.status_code, 204)
            delete_profile.assert_not_called()  # never inside the delete's transaction
            orphan = SocialProfile.objects.get(pk=profile_id)
            self.assertIsNone(orphan.workspace_id)
            for callback in callbacks:
                callback()
        self.assertEqual(delete_profile.call_args.args[0].provider_ref, "KEY-GONE")
        self.assertFalse(SocialProfile.objects.filter(pk=profile_id).exists())

    def test_a_failed_cleanup_never_holds_the_delete(self):
        give_social_profile(self.workspace)
        profile_id = self.profile().pk
        with patch(f"{PROVIDER}.delete_profile", side_effect=RetryableError(detail="ayrshare http=503")), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.delete()
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Workspace.objects.filter(pk=self.workspace.pk).exists())
        # Kept for the daily run.
        self.assertTrue(SocialProfile.objects.filter(pk=profile_id, workspace__isnull=True).exists())
        with patch(f"{PROVIDER}.delete_profile") as delete_profile:
            self.assertEqual(delete_orphan_social_profiles.apply().get(), {"deleted": 1, "kept": 0})
        delete_profile.assert_called_once()

    def test_a_broker_down_never_holds_the_delete(self):
        give_social_profile(self.workspace)
        with patch("apps.integrations.tasks.delete_orphan_social_profiles.delay", side_effect=ConnectionError("broker")), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.delete().status_code, 204)

    def test_without_a_profile_nothing_is_queued(self):
        with patch("apps.integrations.tasks.delete_orphan_social_profiles.delay") as queued, \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.delete().status_code, 204)
        queued.assert_not_called()


class SmokeCommandTests(SocialAccountsHarness, TestCase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command("social_smoke", "--workspace", str(self.workspace.pk), *args, stdout=out)
        return out.getvalue()

    def test_it_creates_links_and_lists_without_printing_the_key(self):
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("KEY-NEVER-PRINTED", "ref-7")), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK) as link, \
                patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")):
            output = self.run_command("--platform", "INSTAGRAM_REELS")
        self.assertIn("Perfil creado", output)
        self.assertIn("ref-7", output)
        self.assertIn(LINK, output)
        self.assertIn("TIKTOK: connected @estudionorte", output)
        self.assertEqual(link.call_args.args[1], "INSTAGRAM_REELS")
        self.assertNotIn("KEY-NEVER-PRINTED", output)

    def test_no_create(self):
        with patch(f"{PROVIDER}.create_profile") as create:
            output = self.run_command("--no-create")
        create.assert_not_called()
        self.assertIn("Sin perfil", output)


@override_settings(SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
class FirstConnectRaceTests(TransactionTestCase):
    """Two first connects at once, with real threads and row locks: one profile at the provider."""

    def test_two_first_connects_create_one_profile(self):
        owner = User.objects.create_user(username="carrera", email="carrera@test.com", password="pw")
        workspace = owner.owned_workspaces.get()
        inside, release = threading.Event(), threading.Event()
        created = []

        def create_profile(*args, **kwargs):
            created.append(1)
            inside.set()
            release.wait(15)
            return CreatedProfile("KEY-RACE", "ref-race")

        results = {}

        def connect(name):
            try:
                api = APIClient()
                api.force_authenticate(owner)
                results[name] = api.post(CONNECT, {"platform": "TIKTOK"}, format="json",
                                         HTTP_X_WORKSPACE_ID=str(workspace.pk)).status_code
            finally:
                connection.close()

        with patch(f"{PROVIDER}.create_profile", side_effect=create_profile), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            first = threading.Thread(target=connect, args=("first",))
            first.start()
            self.assertTrue(inside.wait(10), "the first connect never reached the provider")
            second = threading.Thread(target=connect, args=("second",))
            second.start()
            self.wait_for_a_lock_wait()
            release.set()
            first.join(15)
            second.join(15)

        self.assertEqual(results, {"first": 200, "second": 200})
        self.assertEqual(len(created), 1)
        self.assertEqual(list(SocialProfile.objects.values_list("provider_ref", flat=True)), ["KEY-RACE"])

    def wait_for_a_lock_wait(self, timeout=10):
        """Until the second connect waits for the profile's row lock (or it is too late to)."""
        import time

        deadline = time.monotonic() + timeout
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return
                time.sleep(0.02)
        self.fail("the second connect never waited for the profile's row lock")
