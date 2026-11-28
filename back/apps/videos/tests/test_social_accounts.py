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
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
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
        self.owner = User.objects.create_user(username="duena", email="duena@test.com", password="pw", is_email_verified=True)
        self.workspace = self.owner.owned_workspaces.get()
        # A plan without the limits these tests are not about (members, crossposting), that connects networks.
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=SubscriptionPlan.objects.create(
            name="Redes de prueba", max_members=10, allow_scheduling=True, allow_crossposting=True,
            max_video_duration_seconds=3600, allow_social_accounts=True,
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
        user = User.objects.create_user(username=f"m-{role}", email=f"{role}@test.com", password="pw", is_email_verified=True)
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

    def test_a_provider_failure_is_a_502_with_a_code(self):
        """Ensayo 5: never a 503, which the web app reads as OneCreator's maintenance (K5)."""
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", side_effect=RetryableError(detail="ayrshare http=503")):
            response = self.call("get", LIST)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {
            "error": "No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.", "code": "provider_unavailable",
        })

    def test_a_profile_the_provider_does_not_find_is_kept_on_a_read(self):
        """Review of #187: any member reads; the key is given once: a read never forgets it, it only stops publishing."""
        give_social_profile(self.workspace, "KEY-KEPT")
        with patch(f"{PROVIDER}.accounts", side_effect=PermanentError("profile_not_found", "ayrshare http=403 code=144")), \
                self.assertLogs("apps.integrations.social.accounts", level="ERROR") as logs:
            response = self.call("get", LIST, user=self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual({row["status"] for row in response.json()}, {"disconnected"})
        profile = self.profile()
        self.assertEqual(profile.provider_ref, "KEY-KEPT")
        self.assertEqual(set(profile.networks.values()), {"disconnected"})  # the 409 check now refuses
        self.assertIn("social.profile_not_found", logs.output[0])

    def test_a_failed_read_is_not_tried_again_at_once(self):
        """Review of #187: a slow or failing provider must not take a web worker per request."""
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", side_effect=RetryableError(detail="ayrshare ReadTimeout")) as asked:
            self.assertEqual(self.call("get", LIST).status_code, 502)
            self.assertEqual(self.call("get", LIST).status_code, 502)
        asked.assert_called_once()
        # With a last good answer, that one is served meanwhile.
        cache.clear()
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")):
            self.call("get", LIST)
        cache.delete(f"social:accounts:{self.workspace.pk}")
        with patch(f"{PROVIDER}.accounts", side_effect=RetryableError(detail="ayrshare http=502")) as asked:
            self.assertEqual(self.call("get", LIST).status_code, 502)
            again = self.call("get", LIST)
        asked.assert_called_once()
        self.assertEqual((again.status_code, again.json()[0]["username"]), (200, "@estudionorte"))

    def test_one_read_per_workspace_at_a_time(self):
        """Another request while a read is on its way gets the last answer, without asking the provider."""
        give_social_profile(self.workspace)
        SocialProfile.objects.filter(workspace=self.workspace).update(networks={"TIKTOK": "connected"})
        cache.add(f"social:accounts_reading:{self.workspace.pk}", 1, 30)
        with patch(f"{PROVIDER}.accounts") as asked:
            response = self.call("get", LIST)
        asked.assert_not_called()
        self.assertEqual([row["status"] for row in response.json()], ["connected", "disconnected", "disconnected"])

    def test_with_the_cache_down_each_request_reads_the_provider(self):
        """
        Review of #187: production's fail-open cache answers None (it does not raise) while
        Redis is down; that is not "another read is on its way", or no new link is ever seen.
        """
        give_social_profile(self.workspace)

        class RedisDown:
            def get(self, *args, **kwargs):
                return None

            add = set = delete = get

        with patch("apps.integrations.social.accounts.cache", RedisDown()), \
                patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")) as asked:
            response = self.call("get", LIST)
        asked.assert_called_once()
        self.assertEqual((response.status_code, response.json()[0]["status"]), (200, "connected"))

    def test_reading_the_accounts_waits_little(self):
        from apps.integrations.social.ayrshare import AyrshareProvider
        from apps.integrations.social.base import ACCOUNTS_TIMEOUT_SECONDS

        give_social_profile(self.workspace)
        answer = MagicMock(status_code=200, headers={})
        answer.json.return_value = {"activeSocialAccounts": [], "displayNames": []}
        with override_settings(AYRSHARE_API_KEY="k"), patch("apps.integrations.social.ayrshare.requests.request", return_value=answer) as sent:
            AyrshareProvider().accounts(self.profile())
        self.assertEqual(sent.call_args.kwargs["timeout"], ACCOUNTS_TIMEOUT_SECONDS)
        self.assertLessEqual(sum(ACCOUNTS_TIMEOUT_SECONDS), 15)


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
        self.assertEqual(response.status_code, 502)
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
        lost = PermanentError("profile_not_found", "ayrshare http=403 code=144")
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

    def test_posts_of_another_workspace_never_fail(self):
        """Review of #187: only the workspace that disconnected its network."""
        other_owner = User.objects.create_user(username="otro", email="otro@test.com", password="pw")
        other = other_owner.owned_workspaces.get()
        project = VideoProject.objects.create(workspace=other, uploaded_by=other_owner, title="Otro")
        clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5,
                                        status=VideoClip.Status.COMPLETED, s3_object_key="o.mp4")
        theirs = ScheduledPost.objects.create(video_clip=clip, platform="TIKTOK", status=Status.SCHEDULED,
                                              publish_at="2030-01-01T00:00:00Z", tiktok={"privacy_level": "SELF_ONLY"})
        with patch(f"{PROVIDER}.disconnect"):
            self.assertEqual(self.call("delete", detail("TIKTOK")).status_code, 204)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.SCHEDULED)

    def test_the_posts_are_locked_in_pk_order(self):
        """Review of #187: the order the workspace delete locks them, so the two never deadlock."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.post_in("TIKTOK", Status.SCHEDULED)
        self.post_in("TIKTOK", Status.QUEUED)
        with patch(f"{PROVIDER}.disconnect"), CaptureQueriesContext(connection) as queries:
            self.call("delete", detail("TIKTOK"))
        locking = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"] and "videos_scheduledpost" in query["sql"]]
        self.assertEqual(len(locking), 1)
        self.assertRegex(locking[0], r'SELECT "videos_scheduledpost"\."id" AS "pk" .* ORDER BY 1 ASC FOR UPDATE$')

    def test_a_disconnect_forgets_the_copy_of_the_accounts(self):
        """Review of #187: the next read is the provider's, not the answer from before the disconnect."""
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "connected")
        with patch(f"{PROVIDER}.disconnect"):
            self.call("delete", detail("TIKTOK"))
        with patch(f"{PROVIDER}.accounts", return_value=linked()) as asked:
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "disconnected")
        asked.assert_called_once()

    def test_a_stale_connected_after_a_disconnect_is_not_believed(self):
        """
        Review of #187: a read in flight during the disconnect, or Ayrshare's own copy (60 s), still says
        connected. For 60 s it stays disconnected, so the 409 check keeps refusing posts on it.
        """
        from datetime import timedelta

        from django.utils import timezone

        from apps.integrations.social import accounts as social_accounts

        def read_during_the_disconnect(profile):
            with patch(f"{PROVIDER}.disconnect"):
                social_accounts.disconnect(self.workspace, "TIKTOK")  # lands while this read is on its way
            return linked(TIKTOK="connected", INSTAGRAM_REELS="connected")

        with patch(f"{PROVIDER}.accounts", side_effect=read_during_the_disconnect):
            body = self.call("get", LIST).json()
        self.assertEqual([row["status"] for row in body[:2]], ["disconnected", "connected"])
        self.assertEqual(self.profile().networks["TIKTOK"], "disconnected")
        cache.clear()
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "disconnected")  # the provider's copy
        cache.clear()
        later = timezone.now() + timedelta(seconds=61)
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")), \
                patch("apps.integrations.social.accounts.timezone.now", return_value=later):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "connected")

    def test_connecting_again_is_believed_at_once(self):
        with patch(f"{PROVIDER}.disconnect"):
            self.call("delete", detail("TIKTOK"))
        with patch(f"{PROVIDER}.connect_url", return_value=LINK):
            self.call("post", CONNECT, {"platform": "TIKTOK"})
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected")):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "connected")

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
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {
            "error": "No pudimos hablar con TikTok. Probá de nuevo en unos minutos.", "code": "provider_unavailable",
        })
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

    def on_free_plan(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=SubscriptionPlan.objects.get(name="Gratis"))

    def test_a_plan_without_networks_is_refused_before_calling_the_provider(self):
        """Ensayo 5: on Gratis it created the paid profile the web app never would."""
        self.on_free_plan()
        with patch(f"{PROVIDER}.create_profile") as create, patch(f"{PROVIDER}.connect_url") as link, \
                self.assertRaises(CommandError) as refused:
            self.run_command()
        self.assertEqual(refused.exception.returncode, 1)
        self.assertIn("El plan del espacio (Gratis) no incluye conectar redes", str(refused.exception))
        self.assertIn("--ignore-plan", str(refused.exception))
        create.assert_not_called()
        link.assert_not_called()
        self.assertFalse(SocialProfile.objects.filter(workspace_id=self.workspace.pk).exists())
        # Nor a link to reconnect a profile it kept from a paid plan, as in the web app.
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.connect_url") as link, self.assertRaises(CommandError):
            self.run_command()
        link.assert_not_called()

    def test_ignore_plan_goes_on_for_a_staff_test(self):
        self.on_free_plan()
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile("KEY", "ref-8")), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK), \
                patch(f"{PROVIDER}.accounts", return_value=linked()):
            output = self.run_command("--ignore-plan")
        self.assertIn("--ignore-plan: el plan del espacio (Gratis) no incluye conectar redes; se sigue igual.", output)
        self.assertIn("Perfil creado", output)
        self.assertIn(LINK, output)

    @override_settings(SOCIAL_MAX_PROFILES=1)
    def test_the_profiles_cap_is_refused_before_anything_even_with_ignore_plan(self):
        other = Workspace.objects.create(name="Otro", owner=self.owner)
        give_social_profile(other, "KEY-OTHER")
        for args in ((), ("--ignore-plan",)):
            with self.subTest(args=args), patch(f"{PROVIDER}.create_profile") as create, \
                    self.assertRaises(CommandError) as refused:
                self.run_command(*args)
            self.assertIn("Se llegó al tope de perfiles de OneCreator (SOCIAL_MAX_PROFILES=1)", str(refused.exception))
            create.assert_not_called()
        # Not even the empty row a creation claims first.
        self.assertFalse(SocialProfile.objects.filter(workspace_id=self.workspace.pk).exists())
        # A workspace that has its profile uses no new slot.
        give_social_profile(self.workspace, "KEY-MINE")
        with patch(f"{PROVIDER}.connect_url", return_value=LINK), patch(f"{PROVIDER}.accounts", return_value=linked()):
            self.assertIn("Perfil existente", self.run_command())


@override_settings(SOCIAL_PROFILE_TITLE_PREFIX="oc-test")
class FirstConnectRaceTests(TransactionTestCase):
    """Two first connects at once, with real threads and row locks: one profile at the provider."""

    def test_two_first_connects_create_one_profile(self):
        owner = User.objects.create_user(username="carrera", email="carrera@test.com", password="pw", is_email_verified=True)
        workspace = owner.owned_workspaces.get()
        # Its own plan: a TransactionTestCase empties the tables, the seeded plans too.
        Workspace.objects.filter(pk=workspace.pk).update(subscription_plan=SubscriptionPlan.objects.create(
            name="Redes de carrera", max_members=5, allow_social_accounts=True,
        ))
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
                response = api.post(CONNECT, {"platform": "TIKTOK"}, format="json", HTTP_X_WORKSPACE_ID=str(workspace.pk))
                results[name] = response.status_code
                results[f"{name}_body"] = response.content[:200]
            finally:
                connection.close()

        with patch(f"{PROVIDER}.create_profile", side_effect=create_profile), \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            first = threading.Thread(target=connect, args=("first",))
            first.start()
            self.assertTrue(inside.wait(10), f"the first connect never reached the provider: {results}")
            second = threading.Thread(target=connect, args=("second",))
            second.start()
            self.wait_for_a_lock_wait()
            release.set()
            first.join(15)
            second.join(15)

        self.assertEqual((results.get("first"), results.get("second")), (200, 200), results)
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


class ConnectGateTests(SocialAccountsHarness, TestCase):
    """Franco (#187): connecting networks is for the paid plans; and a verified email, and room for one more profile."""

    def connect(self, platform="TIKTOK", user=None):
        with patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile(f"KEY-{self.workspace.pk}")) as create, \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            response = self.call("post", CONNECT, {"platform": platform}, user=user)
        return response, create

    def test_a_plan_without_it_does_not_connect_but_sees_its_networks(self):
        free = SubscriptionPlan.objects.create(name="Sin redes", max_members=10, allow_social_accounts=False)
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=free)
        response, create = self.connect()
        self.assertEqual((response.status_code, response.json()), (403, {
            "error": "Tu plan no incluye conectar redes. Pasate a un plan pago para conectar las cuentas.",
            "code": "social_accounts",
        }))
        create.assert_not_called()
        self.assertEqual(self.call("get", LIST).status_code, 200)

    def test_the_paid_plans_of_the_seed_connect_and_gratis_does_not(self):
        for name, allowed in (("Gratis", False), ("Creador", True), ("Pro", True), ("Estudio", True)):
            with self.subTest(plan=name):
                SocialProfile.objects.all().delete()
                Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=SubscriptionPlan.objects.get(name=name))
                self.assertEqual(self.connect()[0].status_code, 200 if allowed else 403)

    def test_a_workspace_that_moved_to_gratis_keeps_what_it_connected(self):
        """Decision: what is connected stays (it still publishes, and can be disconnected); connecting again is refused."""
        give_social_profile(self.workspace)
        SocialProfile.objects.filter(workspace=self.workspace).update(networks={"TIKTOK": "connected", "YOUTUBE_SHORTS": "expired"})
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=SubscriptionPlan.objects.get(name="Gratis"))
        with patch(f"{PROVIDER}.accounts", return_value=linked(TIKTOK="connected", YOUTUBE_SHORTS="expired")):
            self.assertEqual(self.call("get", LIST).json()[0]["status"], "connected")
        self.assertEqual(self.connect("YOUTUBE_SHORTS")[0].json()["code"], "social_accounts")  # reconnecting is connecting
        with patch(f"{PROVIDER}.disconnect"):
            self.assertEqual(self.call("delete", detail("YOUTUBE_SHORTS")).status_code, 204)

    def test_without_a_plan_there_are_no_plan_limits(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=None)
        self.assertEqual(self.connect()[0].status_code, 200)

    def test_an_unverified_email_does_not_connect(self):
        self.owner.is_email_verified = False
        self.owner.save(update_fields=["is_email_verified"])
        response, create = self.connect()
        self.assertEqual((response.status_code, response.json().get("code")), (403, "email_not_verified"), response.json())
        create.assert_not_called()

    def test_the_global_cap_of_profiles(self):
        other = User.objects.create_user(username="lleno", email="lleno@test.com", password="pw").owned_workspaces.get()
        give_social_profile(other, "KEY-OTHER")
        with override_settings(SOCIAL_MAX_PROFILES=1):
            response, create = self.connect()
            self.assertEqual((response.status_code, response.json()["code"]), (409, "social_profiles_limit"))
            create.assert_not_called()
            # A workspace that already has its profile still connects its networks.
            self.assertEqual(self.connect(user=None)[0].status_code, 409)
            give_social_profile(self.workspace, "KEY-MINE")
            self.assertEqual(self.connect()[0].status_code, 200)

    def test_a_title_taken_is_its_own_error(self):
        """Review of #187: a creation whose answer was lost is not «probá de nuevo» forever: support fixes it."""
        taken = PermanentError("profile_title_taken", "ayrshare http=400 code=146")
        with patch(f"{PROVIDER}.create_profile", side_effect=taken), \
                self.assertLogs("apps.integrations.social.accounts", level="ERROR") as logs:
            response = self.call("post", CONNECT, {"platform": "TIKTOK"})
        self.assertEqual((response.status_code, response.json()["code"]), (409, "profile_title_taken"))
        self.assertNotIn("Probá de nuevo", response.json()["error"])
        self.assertIn("social.profile_title_taken", logs.output[0])


class CleanupSafetyTests(SocialAccountsHarness, TestCase):
    """Review of #187: the cleanups never delete a profile that someone still uses."""

    def test_one_row_per_provider_profile(self):
        from django.db import IntegrityError, transaction

        give_social_profile(self.workspace, "SHARED")
        other = User.objects.create_user(username="otra", email="otra@test.com", password="pw").owned_workspaces.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SocialProfile.objects.create(workspace=other, provider="ayrshare", provider_ref="SHARED")

    def test_only_the_rows_a_workspace_delete_marked_are_cleaned(self):
        give_social_profile(self.workspace, "STAFF-CLEARED")
        SocialProfile.objects.filter(workspace=self.workspace).update(workspace=None)  # staff emptied the field
        with patch(f"{PROVIDER}.delete_profile") as delete_profile:
            self.assertEqual(delete_orphan_social_profiles.apply().get(), {"deleted": 0, "kept": 0})
        delete_profile.assert_not_called()
        self.assertTrue(SocialProfile.objects.filter(provider_ref="STAFF-CLEARED").exists())

    def test_profiles_never_linked_are_deleted_after_some_days(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.integrations.tasks import clean_social_profiles

        old = timezone.now() - timedelta(days=8)
        give_social_profile(self.workspace, "ABANDONED")
        SocialProfile.objects.filter(workspace=self.workspace).update(created_at=old, networks={})
        linked_owner = User.objects.create_user(username="usa", email="usa@test.com", password="pw")
        give_social_profile(linked_owner.owned_workspaces.get(), "LINKED-AT-THE-PROVIDER")
        SocialProfile.objects.filter(provider_ref="LINKED-AT-THE-PROVIDER").update(created_at=old)
        recent_owner = User.objects.create_user(username="nueva", email="nueva@test.com", password="pw")
        give_social_profile(recent_owner.owned_workspaces.get(), "RECENT")

        def at_the_provider(profile):
            return linked(TIKTOK="connected") if profile.provider_ref == "LINKED-AT-THE-PROVIDER" else linked()

        with patch(f"{PROVIDER}.accounts", side_effect=at_the_provider) as asked, \
                patch(f"{PROVIDER}.delete_profile") as delete_profile:
            result = clean_social_profiles.apply().get()
        self.assertEqual((result["unlinked"], result["unlinked_kept"]), (1, 1))
        self.assertEqual([call.args[0].provider_ref for call in delete_profile.call_args_list], ["ABANDONED"])
        self.assertEqual(asked.call_count, 2)  # the recent one is not even asked
        self.assertFalse(SocialProfile.objects.filter(provider_ref="ABANDONED").exists())
        self.assertIsNotNone(SocialProfile.objects.get(provider_ref="LINKED-AT-THE-PROVIDER").linked_at)
        self.assertTrue(SocialProfile.objects.filter(provider_ref="RECENT").exists())

    def test_a_profile_someone_is_linking_is_not_deleted(self):
        """Review of #187: a link asked for in the last hour may be in use (the provider's list lags 60 s)."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.integrations.tasks import clean_social_profiles

        give_social_profile(self.workspace, "LINKING")
        SocialProfile.objects.filter(workspace=self.workspace).update(
            created_at=timezone.now() - timedelta(days=8), networks={},
        )
        with patch(f"{PROVIDER}.connect_url", return_value=LINK):
            self.assertEqual(self.call("post", CONNECT, {"platform": "TIKTOK"}).status_code, 200)
        with patch(f"{PROVIDER}.accounts", return_value=linked()) as asked, \
                patch(f"{PROVIDER}.delete_profile") as delete_profile:
            result = clean_social_profiles.apply().get()
        self.assertEqual((result["unlinked"], result["unlinked_kept"]), (0, 0))
        asked.assert_not_called()
        delete_profile.assert_not_called()
        self.assertTrue(SocialProfile.objects.filter(provider_ref="LINKING").exists())

        # Past the hour, an abandoned link is cleaned like any other.
        SocialProfile.objects.filter(provider_ref="LINKING").update(connect_requested_at=timezone.now() - timedelta(hours=2))
        with patch(f"{PROVIDER}.accounts", return_value=linked()), patch(f"{PROVIDER}.delete_profile"):
            self.assertEqual(clean_social_profiles.apply().get()["unlinked"], 1)

    def test_a_connect_that_waited_for_a_deleted_row_claims_a_new_one(self):
        """Review of #187: the cleanup deleted the row while the connect waited for its lock: not a 404."""
        from apps.integrations.social import accounts as social_accounts

        give_social_profile(self.workspace, "CLEANED")
        real_filter = SocialProfile.objects.select_for_update
        deleted = []

        def cleanup_meanwhile(*args, **kwargs):
            # The first lock taken by the connect lands after the cleanup deleted the row.
            if not deleted:
                SocialProfile.objects.filter(provider_ref="CLEANED").delete()
                deleted.append(True)
            return real_filter(*args, **kwargs)

        with patch.object(SocialProfile.objects, "select_for_update", side_effect=cleanup_meanwhile), \
                patch(f"{PROVIDER}.create_profile", return_value=CreatedProfile(ref="NEW-KEY", provider_id="r2")) as create, \
                patch(f"{PROVIDER}.connect_url", return_value=LINK):
            profile = social_accounts.ensure_profile(self.workspace)
        create.assert_called_once()
        self.assertEqual((profile.provider_ref, self.profile().provider_ref), ("NEW-KEY", "NEW-KEY"))
        self.assertIsNotNone(self.profile().connect_requested_at)

    def test_a_read_with_a_linked_network_marks_the_profile_linked(self):
        give_social_profile(self.workspace)
        with patch(f"{PROVIDER}.accounts", return_value=linked(INSTAGRAM_REELS="expired")):
            self.call("get", LIST)
        self.assertIsNotNone(self.profile().linked_at)
