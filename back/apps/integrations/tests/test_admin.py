"""Review of #186: the admin never shows a profile's key, and can only replace it."""
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.integrations.models import SocialProfile
from apps.users.models import User


class SocialProfileAdminTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="duena", email="duena@test.com", password="pw")
        self.workspace = owner.owned_workspaces.get()
        self.profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare", provider_ref="SECRET-KEY-1")
        self.url = reverse("admin:integrations_socialprofile_change", args=[self.profile.pk])

    def test_staff_who_may_only_view_never_sees_the_key(self):
        viewer = User.objects.create_user(username="mira", email="mira@test.com", password="pw", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_socialprofile"))
        self.client.force_login(viewer)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "SECRET-KEY-1")

    def test_a_superuser_replaces_it_without_seeing_it(self):
        admin = User.objects.create_superuser(username="root", email="root@test.com", password="pw")
        self.client.force_login(admin)
        page = self.client.get(self.url)
        self.assertNotContains(page, "SECRET-KEY-1")
        form = {"workspace": str(self.workspace.pk), "provider": "ayrshare", "provider_id": "ref-1"}
        self.assertEqual(self.client.post(self.url, {**form, "new_provider_ref": ""}).status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.provider_ref, "SECRET-KEY-1")  # empty keeps it
        self.client.post(self.url, {**form, "new_provider_ref": " KEY-FROM-THE-DASHBOARD "})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.provider_ref, "KEY-FROM-THE-DASHBOARD")


class ProviderOfAProfileTests(TestCase):
    """A profile that exists at its provider keeps it: its ref only means something there."""

    def setUp(self):
        owner = User.objects.create_user(username="dueno", email="dueno@test.com", password="pw")
        self.workspace = owner.owned_workspaces.get()
        admin = User.objects.create_superuser(username="root", email="root@test.com", password="pw")
        self.client.force_login(admin)

    def change(self, profile, **form):
        url = reverse("admin:integrations_socialprofile_change", args=[profile.pk])
        return self.client.post(url, {"workspace": str(self.workspace.pk), "provider_id": "", **form})

    def test_a_ready_profile_does_not_change_its_provider(self):
        profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare", provider_ref="SECRET-KEY-1")
        page = self.change(profile, provider="upload_post", new_provider_ref="")
        self.assertEqual(page.status_code, 200)  # the form again, with the error
        self.assertContains(page, "Este perfil ya existe en Ayrshare")
        self.assertNotContains(page, "SECRET-KEY-1")
        profile.refresh_from_db()
        self.assertEqual((profile.provider, profile.provider_ref), ("ayrshare", "SECRET-KEY-1"))

    def test_a_row_before_its_first_connect_takes_either_provider(self):
        """How a single new workspace goes to Upload-Post (runbook): its row, without a ref, before «Conectar»."""
        profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare")
        self.assertEqual(self.change(profile, provider="upload_post").status_code, 302)
        profile.refresh_from_db()
        self.assertEqual((profile.provider, profile.provider_ref), ("upload_post", ""))

    def test_not_even_with_a_new_ref(self):
        """Review of #199: a post in flight with an unclear key would go out again through the other provider."""
        profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare", provider_ref="SECRET-KEY-1")
        page = self.change(profile, provider="upload_post", new_provider_ref="oc-ws-1")
        self.assertContains(page, "Este perfil ya existe en Ayrshare")
        profile.refresh_from_db()
        self.assertEqual((profile.provider, profile.provider_ref), ("ayrshare", "SECRET-KEY-1"))
        # The same provider with a new ref (a key copied from the provider's panel) still works.
        self.assertEqual(self.change(profile, provider="ayrshare", new_provider_ref="KEY-2").status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.provider_ref, "KEY-2")

    def test_not_while_the_workspace_has_posts_in_flight(self):
        from apps.videos.models import ScheduledPost, VideoClip, VideoProject

        profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare")
        project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.workspace.owner, title="P")
        clip = VideoClip.objects.create(project=project, title="C", start_time=0, end_time=10, s3_object_key="c.mp4")
        post = ScheduledPost.objects.create(video_clip=clip, platform="TIKTOK", status="PROCESSING",
                                            publish_at="2030-01-01T00:00:00Z")
        cases = {
            "processing": {"status": "PROCESSING", "publish_key_unclear": False},
            "queued": {"status": "QUEUED", "publish_key_unclear": False},
            "failed with an unclear key": {"status": "FAILED", "publish_key_unclear": True},
        }
        for name, fields in cases.items():
            with self.subTest(case=name):
                ScheduledPost.objects.filter(pk=post.pk).update(**fields)
                page = self.change(profile, provider="upload_post")
                self.assertContains(page, "publicaciones en camino o con un resultado incierto")
                profile.refresh_from_db()
                self.assertEqual(profile.provider, "ayrshare")
        ScheduledPost.objects.filter(pk=post.pk).update(status="PUBLISHED", publish_key_unclear=False)
        self.assertEqual(self.change(profile, provider="upload_post").status_code, 302)
