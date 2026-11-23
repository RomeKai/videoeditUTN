"""
CORE-06 · CloudflareR2Manager en modo local (USE_R2=False) + formato de keys en modo R2.
En modo local ninguna operación puede crear un cliente boto3.
"""
import os
import re
from unittest.mock import MagicMock

import pytest

from apps.videos.services import storage_service
from apps.videos.services.storage_service import CloudflareR2Manager


@pytest.fixture
def no_boto(monkeypatch):
    def _fail():
        raise AssertionError("Se intentó crear un cliente R2 con USE_R2=False")
    monkeypatch.setattr(storage_service, "_get_r2_client", _fail)


@pytest.fixture
def local_file(settings):
    folder = os.path.join(str(settings.MEDIA_ROOT), "videos", "raw")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "source.mp4")
    with open(path, "wb") as f:
        f.write(b"fake-mp4-bytes")
    return path


# --- USE_R2=False ------------------------------------------------------------------

def test_upload_of_file_inside_media_root_keeps_it_available(no_boto, local_file, settings):
    """La key debe quedar dentro de MEDIA_ROOT y apuntar a un archivo legible."""
    key = CloudflareR2Manager.upload_video(local_file, "user-1", "proj-1")

    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    assert os.path.realpath(key).startswith(media_root)
    with open(key, "rb") as f:
        assert f.read() == b"fake-mp4-bytes"


def test_presigned_url_maps_media_root_to_media_url(no_boto, local_file, settings):
    settings.MEDIA_URL = "/media/"
    url = CloudflareR2Manager.generate_presigned_url(local_file)
    assert url == "/media/videos/raw/source.mp4"
    assert "\\" not in url


@pytest.mark.xfail(
    strict=True,
    reason="Bug storage local: upload_video solo copia a MEDIA_ROOT si el path contiene "
           "'temp' (storage_service.py:54); un archivo de afuera queda referenciado en "
           "su ubicación original y se pierde cuando el llamador lo borra.",
)
def test_upload_of_file_outside_media_root_copies_it_inside(no_boto, settings, tmp_path):
    src = tmp_path / "outside" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"outside-bytes")
    if "temp" in str(src):
        pytest.skip("El TMPDIR contiene 'temp' y activa la heurística: el caso no se puede aislar")

    key = CloudflareR2Manager.upload_video(str(src), "user-1", "proj-1")

    expected_dir = os.path.join(str(settings.MEDIA_ROOT), "videos", "user-1", "proj-1")
    assert os.path.dirname(key) == expected_dir
    src.unlink()  # el llamador limpia su archivo: la copia tiene que sobrevivir
    with open(key, "rb") as f:
        assert f.read() == b"outside-bytes"


def test_upload_without_path_is_rejected(no_boto):
    with pytest.raises(ValueError):
        CloudflareR2Manager.upload_video(None)


def test_presigned_url_for_path_outside_media_root(no_boto, settings):
    settings.MEDIA_URL = "/media/"
    assert CloudflareR2Manager.generate_presigned_url("other/clip.mp4") == "/media/other/clip.mp4"


def test_download_copies_local_file(no_boto, local_file, tmp_path):
    dest = tmp_path / "copy.mp4"
    CloudflareR2Manager.download_file("ignored-bucket", local_file, str(dest))
    assert dest.read_bytes() == b"fake-mp4-bytes"


def test_download_to_same_path_is_noop(no_boto, local_file):
    CloudflareR2Manager.download_file("ignored-bucket", local_file, local_file)
    assert os.path.exists(local_file)


def test_delete_removes_local_file(no_boto, local_file):
    CloudflareR2Manager.delete_object(local_file)
    assert not os.path.exists(local_file)


def test_delete_missing_local_file_does_not_fail(no_boto, tmp_path):
    CloudflareR2Manager.delete_object(str(tmp_path / "no-existe.mp4"))


def test_upload_round_trip_local(no_boto, local_file, tmp_path):
    key = CloudflareR2Manager.upload_video(local_file, "u", "p")
    dest = tmp_path / "back.mp4"
    CloudflareR2Manager.download_file("bucket", key, str(dest))
    assert dest.read_bytes() == b"fake-mp4-bytes"


# --- USE_R2=True (cliente mockeado, sin red) ---------------------------------------------

@pytest.fixture
def r2_client(monkeypatch, settings):
    settings.USE_R2 = True
    settings.CLOUDFLARE_R2_BUCKET_NAME = "test-bucket"
    client = MagicMock()
    monkeypatch.setattr(storage_service, "_get_r2_client", lambda: client)
    return client


def test_r2_upload_builds_namespaced_key(r2_client, local_file):
    key = CloudflareR2Manager.upload_video(local_file, "user-1", "proj-1")

    assert re.fullmatch(r"videos/user-1/proj-1/[0-9a-f-]{36}\.mp4", key)
    r2_client.upload_file.assert_called_once_with(local_file, "test-bucket", key)


def test_r2_upload_without_folder(r2_client, local_file):
    key = CloudflareR2Manager.upload_video(local_file, "user-1")
    assert re.fullmatch(r"videos/user-1/[0-9a-f-]{36}\.mp4", key)


def test_r2_delete_uses_bucket(r2_client):
    CloudflareR2Manager.delete_object("videos/u/x.mp4")
    r2_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="videos/u/x.mp4")
