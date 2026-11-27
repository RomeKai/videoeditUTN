"""
Brand kits: duplicating one with its files, and one default per workspace.
"""

import os
import shutil
import tempfile
import threading
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import BrandKit
from apps.videos.services import brand_kit_service, storage_service

User = get_user_model()

FILE_FIELDS = ("primary_font", "secondary_font", "watermark_logo", "intro_video", "outro_video")
COPY_FILE = "apps.videos.services.brand_kit_service.copy_stored_file"
BRAND_KITS = reverse("brandkit-list")


def detail(kit):
    return reverse("brandkit-detail", args=[kit.pk])


def duplicate(kit):
    return reverse("brandkit-duplicate", args=[kit.pk])


def header(workspace):
    return {"HTTP_X_WORKSPACE_ID": str(workspace.pk)}


def make_user(name):
    return User.objects.create_user(username=name, email=f"{name}@test.com", password="pw")


def join(workspace, user, role):
    return WorkspaceMember.objects.create(
        workspace=workspace, user=user, role=role, status=WorkspaceMember.Status.ACTIVE,
    )


def full_kit(workspace, user, **fields):
    """A kit with every field away from its default and its five files stored."""
    kit = BrandKit.objects.create(
        workspace=workspace, user=user, name="Estudio", description="El kit del estudio",
        primary_color="#111111", secondary_color="#222222", accent_color="#333333", **fields,
    )
    for field in FILE_FIELDS:
        getattr(kit, field).save(f"{field}.bin", ContentFile(field.encode()), save=False)
    kit.save()
    return kit


def stored_files(root):
    return sorted(os.path.join(folder, name) for folder, _, names in os.walk(root) for name in names)


class MediaRootHarness(APITestCase):
    """Each test stores its files in a directory of its own."""

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="brand-kits-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=self.media)
        settings.enable()
        self.addCleanup(settings.disable)
        self.alice = make_user("alice")
        self.home = self.alice.owned_workspaces.get()
        self.client.force_authenticate(user=self.alice)


class DuplicateBrandKitTests(MediaRootHarness):
    def test_the_copy_has_every_field_and_its_own_files(self):
        kit = full_kit(self.home, self.alice, is_default=True)

        response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 201, response.content[:300])
        copy = BrandKit.objects.get(pk=response.data["id"])
        self.assertNotEqual(copy.pk, kit.pk)
        self.assertEqual(response.data["name"], "Copia de Estudio")
        self.assertEqual(copy.workspace, self.home)
        self.assertEqual(copy.user, self.alice)
        for field in ("description", "primary_color", "secondary_color", "accent_color"):
            self.assertEqual(getattr(copy, field), getattr(kit, field), field)
        # Never the default, and the original still is.
        self.assertIs(response.data["is_default"], False)
        self.assertTrue(BrandKit.objects.get(pk=kit.pk).is_default)
        for field in FILE_FIELDS:
            original, copied = getattr(kit, field), getattr(copy, field)
            self.assertTrue(copied.name, field)
            self.assertNotEqual(copied.name, original.name, field)
            # The folder of each kit, the same file name (no storage suffix), the same bytes.
            folder = original.name.split("/")[1]
            self.assertEqual(original.name, f"assets/{folder}/{kit.pk}/{field}.bin")
            self.assertEqual(copied.name, f"assets/{folder}/{copy.pk}/{field}.bin")
            self.assertEqual(copied.read(), field.encode())
            self.assertIn(copied.name, response.data[field])
        self.assertEqual(len(stored_files(self.media)), 10)
        # Every model field is either copied or set on purpose: a new field is copied without touching the service.
        self.assertEqual(
            {f.name for f in BrandKit._meta.concrete_fields} - brand_kit_service._NOT_COPIED,
            {"description", "primary_color", "secondary_color", "accent_color", *FILE_FIELDS},
        )

    def test_a_kit_without_files_and_a_long_name(self):
        kit = BrandKit.objects.create(workspace=self.home, name="N" * 100)

        response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.data["name"], ("Copia de " + "N" * 100)[:100])
        for field in FILE_FIELDS:
            self.assertIsNone(response.data[field])
        self.assertEqual(stored_files(self.media), [])

    def test_deleting_the_original_and_its_files_keeps_the_copys_files(self):
        kit = full_kit(self.home, self.alice)
        copy = BrandKit.objects.get(pk=self.client.post(duplicate(kit), **header(self.home)).data["id"])

        self.assertEqual(self.client.delete(detail(kit), **header(self.home)).status_code, 204)
        for field in FILE_FIELDS:
            file = getattr(kit, field)
            file.storage.delete(file.name)

        for field in FILE_FIELDS:
            self.assertEqual(getattr(copy, field).read(), field.encode(), field)

    def test_a_copy_of_a_copy_and_of_old_files_keeps_the_file_names(self):
        # Files stored before the folder per kit: straight under upload_to.
        kit = BrandKit.objects.create(workspace=self.home, name="Viejo")
        storage = kit.watermark_logo.storage
        storage.save("assets/logos/logo.png", ContentFile(b"logo"))
        storage.save("assets/fonts/Inter-Bold.ttf", ContentFile(b"font"))
        BrandKit.objects.filter(pk=kit.pk).update(
            watermark_logo="assets/logos/logo.png", primary_font="assets/fonts/Inter-Bold.ttf",
        )

        first = BrandKit.objects.get(pk=self.client.post(duplicate(kit), **header(self.home)).data["id"])
        second = BrandKit.objects.get(pk=self.client.post(duplicate(kit), **header(self.home)).data["id"])
        nested = BrandKit.objects.get(pk=self.client.post(duplicate(first), **header(self.home)).data["id"])

        for copy in (first, second, nested):
            self.assertEqual(copy.watermark_logo.name, f"assets/logos/{copy.pk}/logo.png")
            self.assertEqual(copy.primary_font.name, f"assets/fonts/{copy.pk}/Inter-Bold.ttf")
            self.assertEqual(copy.watermark_logo.read(), b"logo")
        self.assertEqual(BrandKit.objects.get(pk=kit.pk).watermark_logo.name, "assets/logos/logo.png")
        self.assertEqual(len(stored_files(self.media)), 8)

    def test_an_upload_goes_to_the_folder_of_its_kit_with_its_name(self):
        for name in ("Uno", "Dos"):
            response = self.client.post(
                BRAND_KITS, {"name": name, "primary_font": ContentFile(b"font", name="Inter-Bold.ttf")},
                format="multipart", **header(self.home),
            )
            self.assertEqual(response.status_code, 201, response.content[:300])
            kit = BrandKit.objects.get(pk=response.data["id"])
            self.assertEqual(kit.primary_font.name, f"assets/fonts/{kit.pk}/Inter-Bold.ttf")
            self.assertTrue(response.data["primary_font"].endswith(f"/{kit.pk}/Inter-Bold.ttf"))

    def test_later_copies_are_numbered_within_the_workspace(self):
        boss = make_user("boss")
        BrandKit.objects.create(workspace=boss.owned_workspaces.get(), name="Copia de Estudio")
        kit = BrandKit.objects.create(workspace=self.home, name="Estudio")

        names = [self.client.post(duplicate(kit), **header(self.home)).data["name"] for _ in range(3)]

        self.assertEqual(names, ["Copia de Estudio", "Copia 2 de Estudio", "Copia 3 de Estudio"])
        # A freed number is used again, and a renamed kit takes no number.
        BrandKit.objects.filter(workspace=self.home, name="Copia 2 de Estudio").delete()
        self.assertEqual(self.client.post(duplicate(kit), **header(self.home)).data["name"], "Copia 2 de Estudio")

    def test_numbered_copies_of_a_long_name_are_cut_and_differ(self):
        kit = BrandKit.objects.create(workspace=self.home, name="N" * 100)

        names = [self.client.post(duplicate(kit), **header(self.home)).data["name"] for _ in range(2)]

        self.assertEqual(names, [("Copia de " + "N" * 100)[:100], ("Copia 2 de " + "N" * 100)[:100]])

    def test_a_kit_of_another_workspace_is_a_404(self):
        boss = make_user("boss")
        theirs = full_kit(boss.owned_workspaces.get(), boss)

        response = self.client.post(duplicate(theirs), **header(self.home))

        self.assertEqual(response.status_code, 404, response.content[:300])
        self.assertEqual(BrandKit.objects.count(), 1)
        self.assertEqual(len(stored_files(self.media)), 5)

    def test_roles(self):
        boss = make_user("boss")
        team = Workspace.objects.create(name="Equipo", owner=boss)
        kit = BrandKit.objects.create(workspace=team, name="Del equipo")
        membership = join(team, self.alice, WorkspaceMember.Role.VIEWER)

        response = self.client.post(duplicate(kit), **header(team))

        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertIn("detail", response.data)
        self.assertEqual(BrandKit.objects.count(), 1)

        WorkspaceMember.objects.filter(pk=membership.pk).update(role=WorkspaceMember.Role.EDITOR)
        self.assertEqual(self.client.post(duplicate(kit), **header(team)).status_code, 201)

    def test_a_failed_file_copy_leaves_nothing_behind(self):
        kit = full_kit(self.home, self.alice)
        before = stored_files(self.media)
        real_copy, calls = storage_service.copy_stored_file, []

        def third_copy_fails(storage, source, dest):
            calls.append(dest)
            if len(calls) == 3:
                raise storage_service.StoredFileCopyError("ClientError")
            real_copy(storage, source, dest)

        with patch(COPY_FILE, side_effect=third_copy_fails):
            response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 502, response.content[:300])
        self.assertEqual(response.data["code"], "brand_kit_copy_failed")
        self.assertTrue(response.data["error"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(list(BrandKit.objects.values_list("pk", flat=True)), [kit.pk])
        # The two files already copied are gone; the original's are intact.
        self.assertEqual(stored_files(self.media), before)

    def test_a_missing_source_file_is_the_same_error(self):
        kit = full_kit(self.home, self.alice)
        kit.intro_video.storage.delete(kit.intro_video.name)
        before = stored_files(self.media)

        response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 502, response.content[:300])
        self.assertEqual(BrandKit.objects.count(), 1)
        self.assertEqual(stored_files(self.media), before)

    def test_a_failed_insert_deletes_the_copied_files(self):
        kit = full_kit(self.home, self.alice)
        before = stored_files(self.media)

        with patch.object(BrandKit.objects, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                brand_kit_service.duplicate_brand_kit(kit, user=self.alice)

        self.assertEqual(stored_files(self.media), before)


class SameNameStorage(FileSystemStorage):
    """A storage that overwrites: the "available" name is the one asked for."""

    def get_available_name(self, name, max_length=None):
        return name


class SameNameStorageTests(MediaRootHarness):
    """A copy must never share, or cost, the files of the original kit."""

    def kit_on_same_name_storage(self):
        kit = full_kit(self.home, self.alice)
        storage = SameNameStorage()
        for name in FILE_FIELDS:
            patcher = patch.object(BrandKit._meta.get_field(name), "storage", storage)
            patcher.start()
            self.addCleanup(patcher.stop)
        return BrandKit.objects.get(pk=kit.pk)

    def assert_originals_intact(self, kit):
        for name in FILE_FIELDS:
            with open(os.path.join(self.media, getattr(kit, name).name), "rb") as stored:
                self.assertEqual(stored.read(), name.encode(), name)

    def test_a_storage_that_overwrites_still_gives_the_copy_its_own_files(self):
        kit = self.kit_on_same_name_storage()

        response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 201, response.content[:300])
        copy = BrandKit.objects.get(pk=response.data["id"])
        for name in FILE_FIELDS:
            self.assertNotEqual(getattr(copy, name).name, getattr(kit, name).name, name)
        self.assertEqual(len(stored_files(self.media)), 10)
        self.assert_originals_intact(kit)

    def test_a_copy_with_the_name_of_its_original_is_refused(self):
        kit = self.kit_on_same_name_storage()
        before = stored_files(self.media)

        # The destination is forced to be the source: the folder per kit never gives it.
        with patch.object(brand_kit_service, "_copy_file_name", side_effect=lambda field, name, copy_id: name):
            response = self.client.post(duplicate(kit), **header(self.home))

        self.assertEqual(response.status_code, 502, response.content[:300])
        self.assertEqual(response.data["code"], "brand_kit_copy_failed")
        self.assertEqual(list(BrandKit.objects.values_list("pk", flat=True)), [kit.pk])
        self.assertEqual(stored_files(self.media), before)
        self.assert_originals_intact(kit)

    def test_the_cleanup_never_deletes_a_file_of_the_original(self):
        # The copy step is taken out, so every "copy" keeps its original's name.
        kit = self.kit_on_same_name_storage()
        before = stored_files(self.media)

        with (
            patch(COPY_FILE),
            patch.object(brand_kit_service, "_copy_file_name", side_effect=lambda field, name, copy_id: name),
            patch.object(BrandKit.objects, "create", side_effect=IntegrityError("boom")),
        ):
            with self.assertRaises(IntegrityError):
                brand_kit_service.duplicate_brand_kit(kit, user=self.alice)

        self.assertEqual(stored_files(self.media), before)
        self.assert_originals_intact(kit)

    def test_delete_copies_skips_the_source_names(self):
        storage = MagicMock()

        brand_kit_service._delete_copies(
            [(storage, "assets/logos/a.png"), (storage, "assets/logos/a_copy.png")],
            {"assets/logos/a.png"},
        )

        storage.delete.assert_called_once_with("assets/logos/a_copy.png")


class DeleteFilesTests(MediaRootHarness):
    """A deleted kit, and a replaced file, leave nothing in the storage; a shared file stays."""

    def exists(self, name):
        return os.path.exists(os.path.join(self.media, name))

    def test_deleting_a_kit_deletes_its_files_after_the_commit(self):
        kit = full_kit(self.home, self.alice)
        other = full_kit(self.home, self.alice)
        self.assertEqual(len(stored_files(self.media)), 10)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(detail(kit), **header(self.home))
            # Not before the commit: a rolled back delete keeps its files.
            self.assertEqual(len(stored_files(self.media)), 10)

        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(len(stored_files(self.media)), 5)
        for field in FILE_FIELDS:
            self.assertFalse(self.exists(getattr(kit, field).name), field)
            self.assertEqual(getattr(other, field).read(), field.encode(), field)

    def test_a_copy_keeps_its_files_when_the_original_is_deleted(self):
        kit = full_kit(self.home, self.alice)
        copy = BrandKit.objects.get(pk=self.client.post(duplicate(kit), **header(self.home)).data["id"])

        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(detail(kit), **header(self.home))

        self.assertEqual(len(stored_files(self.media)), 5)
        for field in FILE_FIELDS:
            self.assertEqual(getattr(copy, field).read(), field.encode(), field)

    def test_a_file_that_another_kit_uses_is_kept(self):
        # Old data and seeds: two rows on one name, also in another field.
        kit = full_kit(self.home, self.alice)
        boss = make_user("boss")
        sharing = BrandKit.objects.create(
            workspace=boss.owned_workspaces.get(), name="Comparte",
            watermark_logo=kit.watermark_logo.name, secondary_font=kit.primary_font.name,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(detail(kit), **header(self.home)).status_code, 204)

        self.assertTrue(self.exists(kit.watermark_logo.name))
        self.assertTrue(self.exists(kit.primary_font.name))
        self.assertEqual(len(stored_files(self.media)), 2)

        # The last kit that uses them takes them.
        with self.captureOnCommitCallbacks(execute=True):
            sharing.delete()
        self.assertEqual(stored_files(self.media), [])

    def test_a_storage_error_does_not_fail_the_delete_and_logs_ids_only(self):
        kit = full_kit(self.home, self.alice)
        pk = kit.pk

        with patch.object(FileSystemStorage, "delete", side_effect=OSError("/secret/path busy")):
            with self.assertLogs("apps.videos.services.brand_kit_service", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.delete(detail(kit), **header(self.home))

        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertFalse(BrandKit.objects.filter(pk=pk).exists())
        self.assertEqual(len(logs.output), 5)
        for line in logs.output:
            self.assertIn(f"brand_kit.file_cleanup_failed brand_kit_id={pk} error=OSError", line)
            self.assertNotIn("secret", line)
            self.assertNotIn("assets/", line)

    def test_replacing_or_removing_a_file_deletes_the_old_one(self):
        kit = full_kit(self.home, self.alice)
        old_logo, old_intro, kept_font = kit.watermark_logo.name, kit.intro_video.name, kit.primary_font.name

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                detail(kit), {"watermark_logo": _png("nuevo.png"), "intro_video": "", "name": "Otro"},
                format="multipart", **header(self.home),
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        kit.refresh_from_db()
        self.assertEqual(kit.watermark_logo.name, f"assets/logos/{kit.pk}/nuevo.png")
        self.assertFalse(kit.intro_video)
        self.assertFalse(self.exists(old_logo))
        self.assertFalse(self.exists(old_intro))
        self.assertTrue(self.exists(kept_font))
        self.assertEqual(len(stored_files(self.media)), 4)

    def test_a_replaced_file_that_another_kit_uses_is_kept(self):
        kit = full_kit(self.home, self.alice)
        old_logo = kit.watermark_logo.name
        BrandKit.objects.create(workspace=self.home, name="Comparte", watermark_logo=old_logo)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                detail(kit), {"watermark_logo": _png("nuevo.png")}, format="multipart", **header(self.home),
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(self.exists(old_logo))

    def test_an_edit_without_files_deletes_nothing(self):
        kit = full_kit(self.home, self.alice)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(detail(kit), {"name": "Otro"}, format="multipart", **header(self.home))

        self.assertEqual(len(stored_files(self.media)), 5)


def _png(name):
    """A 1x1 PNG: the logo is an ImageField, so the upload has to be an image."""
    import base64

    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    return ContentFile(data, name=name)


class BucketStorage(Storage):
    """Stands for a bucket storage (django-storages): no local paths."""

    bucket_name = "kits-bucket"
    location = "media"


class CopyStoredFileTests(APITestCase):
    def test_a_bucket_storage_copies_server_side(self):
        client = MagicMock()
        with override_settings(USE_R2=True), patch.object(storage_service, "_get_r2_client", return_value=client):
            storage_service.copy_stored_file(BucketStorage(), "assets/logos/a.png", "assets/logos/a_copy.png")

        client.copy_object.assert_called_once_with(
            Bucket="kits-bucket", Key="media/assets/logos/a_copy.png",
            CopySource={"Bucket": "kits-bucket", "Key": "media/assets/logos/a.png"},
        )
        client.download_file.assert_not_called()
        client.upload_file.assert_not_called()

    def test_the_same_name_is_refused_before_copying(self):
        client = MagicMock()
        with override_settings(USE_R2=True), patch.object(storage_service, "_get_r2_client", return_value=client):
            with self.assertRaises(storage_service.StoredFileCopyError):
                storage_service.copy_stored_file(BucketStorage(), "a.png", "a.png")
        client.copy_object.assert_not_called()

    def test_a_bucket_error_is_a_copy_error(self):
        client = MagicMock()
        client.copy_object.side_effect = RuntimeError("secret detail")
        with override_settings(USE_R2=True), patch.object(storage_service, "_get_r2_client", return_value=client):
            with self.assertRaises(storage_service.StoredFileCopyError) as raised:
                storage_service.copy_stored_file(BucketStorage(), "a.png", "b.png")
        self.assertEqual(str(raised.exception), "RuntimeError")


class DefaultBrandKitTests(MediaRootHarness):
    def defaults(self, workspace=None):
        return list(BrandKit.objects.filter(workspace=workspace or self.home, is_default=True).values_list("name", flat=True))

    def setUp(self):
        super().setUp()
        self.first = BrandKit.objects.create(workspace=self.home, name="Primero", is_default=True)
        self.second = BrandKit.objects.create(workspace=self.home, name="Segundo")

    def test_marking_a_kit_unmarks_the_previous_default(self):
        for method, body in (("patch", {"is_default": True}), ("put", {"name": "Primero", "is_default": True})):
            target = self.second if method == "patch" else self.first
            with self.subTest(method=method):
                response = getattr(self.client, method)(detail(target), body, format="json", **header(self.home))
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertIs(response.data["is_default"], True)
                self.assertEqual(self.defaults(), [target.name])

    def test_multipart_as_the_web_app_sends_it(self):
        response = self.client.patch(detail(self.second), {"is_default": "true"}, format="multipart", **header(self.home))

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.defaults(), ["Segundo"])

    def test_creating_a_default_kit_unmarks_the_previous_one(self):
        response = self.client.post(BRAND_KITS, {"name": "Tercero", "is_default": True}, format="json", **header(self.home))

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(self.defaults(), ["Tercero"])

    def test_the_default_of_another_workspace_is_not_touched(self):
        boss = make_user("boss")
        team = Workspace.objects.create(name="Equipo", owner=boss)
        BrandKit.objects.create(workspace=team, name="Del equipo", is_default=True)

        self.client.patch(detail(self.second), {"is_default": True}, format="json", **header(self.home))

        self.assertEqual(self.defaults(team), ["Del equipo"])
        self.assertEqual(self.defaults(), ["Segundo"])

    def test_unmarking_the_default_leaves_the_workspace_without_one(self):
        response = self.client.patch(detail(self.first), {"is_default": False}, format="json", **header(self.home))

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.defaults(), [])

    def test_an_edit_that_does_not_send_is_default_changes_no_default(self):
        response = self.client.patch(detail(self.second), {"name": "Otro nombre"}, format="json", **header(self.home))

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.defaults(), ["Primero"])

    def test_deleting_the_default_promotes_no_other_kit(self):
        self.assertEqual(self.client.delete(detail(self.first), **header(self.home)).status_code, 204)

        self.assertEqual(self.defaults(), [])
        self.assertTrue(BrandKit.objects.filter(pk=self.second.pk).exists())

    def test_the_database_refuses_a_second_default(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BrandKit.objects.filter(pk=self.second.pk).update(is_default=True)


class ConcurrentDefaultTests(TransactionTestCase):
    def test_two_requests_at_once_leave_exactly_one_default(self):
        alice = make_user("alice")
        home = alice.owned_workspaces.get()
        kits = [BrandKit.objects.create(workspace=home, name=f"Kit {index}") for index in range(2)]
        barrier = threading.Barrier(2)
        statuses = []

        def mark(kit):
            try:
                client = APIClient()
                client.force_authenticate(user=alice)
                barrier.wait(timeout=10)
                statuses.append(client.patch(detail(kit), {"is_default": True}, format="json", **header(home)).status_code)
            finally:
                connections.close_all()

        for _ in range(5):
            BrandKit.objects.filter(workspace=home).update(is_default=False)
            statuses.clear()
            barrier.reset()
            threads = [threading.Thread(target=mark, args=(kit,)) for kit in kits]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            self.assertEqual(statuses, [200, 200])
            self.assertEqual(BrandKit.objects.filter(workspace=home, is_default=True).count(), 1)

    def test_the_lock_makes_the_second_request_wait(self):
        """Without the workspace lock the second request would not wait: it would fail on the constraint."""
        alice = make_user("alice")
        home = alice.owned_workspaces.get()
        first = BrandKit.objects.create(workspace=home, name="Primero")
        second = BrandKit.objects.create(workspace=home, name="Segundo")
        result = {}

        def mark_second():
            try:
                client = APIClient()
                client.force_authenticate(user=alice)
                result["status"] = client.patch(detail(second), {"is_default": True}, format="json", **header(home)).status_code
            finally:
                connections.close_all()

        with transaction.atomic():
            brand_kit_service.lock_workspace_default(home.pk)
            brand_kit_service.demote_other_defaults(home.pk, keep_pk=first.pk)
            BrandKit.objects.filter(pk=first.pk).update(is_default=True)
            thread = threading.Thread(target=mark_second)
            thread.start()
            thread.join(timeout=1.5)
            self.assertTrue(thread.is_alive(), "the request did not wait for the lock")
        thread.join(timeout=30)

        self.assertEqual(result["status"], 200)
        self.assertEqual(
            list(BrandKit.objects.filter(workspace=home, is_default=True).values_list("name", flat=True)), ["Segundo"],
        )


BEFORE = [("videos", "0018_clip_thumbnail_object_key")]
AFTER = [("videos", "0019_un_solo_brand_kit_default")]


class OneDefaultMigrationTests(TransactionTestCase):
    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_workspaces_with_several_defaults_keep_the_oldest(self):
        old_apps = self.migrate(BEFORE)
        Workspace_ = old_apps.get_model("users", "Workspace")
        Kit = old_apps.get_model("videos", "BrandKit")
        user_model = old_apps.get_model(*User._meta.label_lower.split("."))
        owner = user_model.objects.create(username="kits", email="kits@test.com", password="!")
        several, single, none = (Workspace_.objects.create(name=name, owner_id=owner.pk) for name in "abc")

        def kit(workspace, name, is_default):
            return Kit.objects.create(workspace_id=workspace.pk, name=name, is_default=is_default).pk

        kit(several, "sin marcar", False)
        oldest = kit(several, "viejo", True)
        kit(several, "medio", True)
        kit(several, "nuevo", True)
        only = kit(single, "único", True)
        kit(none, "ninguno", False)

        new_apps = self.migrate(AFTER)
        NewKit = new_apps.get_model("videos", "BrandKit")

        self.assertEqual(NewKit.objects.count(), 6)
        self.assertEqual(set(NewKit.objects.filter(is_default=True).values_list("pk", flat=True)), {oldest, only})

        # Reversing drops the constraint and keeps the data.
        back_apps = self.migrate(BEFORE)
        OldKit = back_apps.get_model("videos", "BrandKit")
        OldKit.objects.filter(workspace_id=several.pk).update(is_default=True)
        self.assertEqual(OldKit.objects.filter(is_default=True).count(), 5)
