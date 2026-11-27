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
