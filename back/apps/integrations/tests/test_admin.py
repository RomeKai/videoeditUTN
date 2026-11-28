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

    def test_a_new_ref_may_come_with_its_provider(self):
        profile = SocialProfile.objects.create(workspace=self.workspace, provider="ayrshare", provider_ref="SECRET-KEY-1")
        self.assertEqual(self.change(profile, provider="upload_post", new_provider_ref="oc-ws-1").status_code, 302)
        profile.refresh_from_db()
        self.assertEqual((profile.provider, profile.provider_ref), ("upload_post", "oc-ws-1"))
