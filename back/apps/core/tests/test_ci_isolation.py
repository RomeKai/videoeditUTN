import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.core.mail import send_mail

from apps.videos.services.storage_service import CloudflareR2Manager


def test_controlled_subprocess_cannot_reach_external_network():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import socket; "
                "client = socket.create_connection(('1.1.1.1', 443), timeout=2); "
                "client.close()"
            ),
        ],
        check=True,
        timeout=5,
    )


def test_external_socket_is_blocked_before_underlying_connect(network_guard):
    called = False

    def fake_connect(sock, address):
        nonlocal called
        called = True

    with pytest.raises(RuntimeError, match="External network access is disabled"):
        network_guard.connect(fake_connect, object(), ("api.example.com", 443))

    assert called is False
    network_guard.acknowledge_expected_blocks()


def test_external_connect_ex_is_blocked_before_underlying_connect(network_guard):
    called = False

    def fake_connect_ex(sock, address):
        nonlocal called
        called = True
        return 0

    with pytest.raises(RuntimeError, match="External network access is disabled"):
        network_guard.connect_ex(fake_connect_ex, object(), ("api.example.com", 443))

    assert called is False
    network_guard.acknowledge_expected_blocks()


def test_external_connect_ex_is_blocked_through_socket_api(network_guard):
    with socket.socket() as client:
        with pytest.raises(RuntimeError, match="External network access is disabled"):
            client.connect_ex(("203.0.113.1", 443))

    network_guard.acknowledge_expected_blocks()


def test_loopback_postgresql_connection_is_allowed(network_guard):
    calls = []

    def fake_connect(sock, address):
        calls.append(address)
        return "connected"

    result = network_guard.connect(fake_connect, object(), ("127.0.0.1", 5432))

    assert result == "connected"
    assert calls == [("127.0.0.1", 5432)]


def test_caught_external_attempt_still_fails_guard(network_guard):
    start = network_guard.checkpoint()

    try:
        network_guard.connect(lambda *_: None, object(), ("openai.com", 443))
    except RuntimeError:
        pass

    with pytest.raises(AssertionError, match="openai.com:443"):
        network_guard.assert_no_blocked_attempts_since(start)

    network_guard.acknowledge_expected_blocks()


def test_media_root_is_isolated_per_test(tmp_path):
    media_root = Path(settings.MEDIA_ROOT)

    assert media_root.parent == tmp_path
    assert media_root.name == "media"
    assert media_root != Path(settings.BASE_DIR) / "media"


def test_email_stays_in_process():
    send_mail(
        "CI isolation",
        "This message must not leave the process.",
        "ci@example.invalid",
        ["recipient@example.invalid"],
    )

    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
    assert len(mail.outbox) == 1


def test_celery_runs_eager_with_in_memory_backends():
    from backend.celery import app

    assert settings.CELERY_TASK_ALWAYS_EAGER is True
    assert settings.CELERY_TASK_EAGER_PROPAGATES is True
    assert app.conf.task_always_eager is True
    assert app.conf.task_eager_propagates is True
    assert app.conf.broker_url == "memory://"
    assert app.conf.result_backend == "cache+memory://"


def test_r2_uses_local_mode_without_a_client(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic-video")

    def reject_client():
        raise AssertionError("R2 client must not be created in CI")

    monkeypatch.setattr(
        "apps.videos.services.storage_service._get_r2_client",
        reject_client,
    )

    object_key = CloudflareR2Manager.upload_video(str(source), "ci-user")
    url = CloudflareR2Manager.generate_presigned_url(object_key)

    assert object_key == str(source)
    assert url.startswith(settings.MEDIA_URL)


def test_r2_delete_uses_local_mode_without_a_client(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic-video")

    def reject_client():
        raise AssertionError("R2 client must not be created in CI")

    monkeypatch.setattr(
        "apps.videos.services.storage_service._get_r2_client",
        reject_client,
    )

    CloudflareR2Manager.delete_object(str(source))

    assert source.exists() is False


def test_aws_metadata_lookup_is_disabled():
    assert os.environ["AWS_EC2_METADATA_DISABLED"].lower() == "true"
