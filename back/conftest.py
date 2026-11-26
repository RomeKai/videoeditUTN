import ipaddress
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest


os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _capacity_test_redis_ports() -> set[int]:
    """
    Port of the local Valkey the capacity tests use (AICORE-11), when
    CAPACITY_TEST_REDIS_URL is set. Still loopback-only, like PostgreSQL.
    """
    url = os.environ.get("CAPACITY_TEST_REDIS_URL")
    if not url:
        return set()
    from urllib.parse import urlparse

    return {urlparse(url).port or 6379}


@dataclass
class NetworkGuard:
    """Blocks test-process network traffic except local PostgreSQL (and the capacity tests' Valkey)."""

    blocked_attempts: list[str] = field(default_factory=list)
    allowed_hostnames = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})
    allowed_ports = frozenset({5432} | _capacity_test_redis_ports())

    def checkpoint(self) -> int:
        return len(self.blocked_attempts)

    def acknowledge_expected_blocks(self) -> None:
        self.blocked_attempts.clear()

    def assert_no_blocked_attempts_since(self, checkpoint: int) -> None:
        attempts = self.blocked_attempts[checkpoint:]
        if attempts:
            raise AssertionError(
                "Application attempted blocked external network access: "
                + ", ".join(attempts)
            )

    def connect(self, original: Callable, sock, address):
        if isinstance(address, str):
            return original(sock, address)

        host, port = address[0], address[1]
        if self._is_allowed(host, port):
            return original(sock, address)

        self._block(host, port)

    def connect_ex(self, original: Callable, sock, address):
        if isinstance(address, str):
            return original(sock, address)

        host, port = address[0], address[1]
        if self._is_allowed(host, port):
            return original(sock, address)

        self._block(host, port)

    def create_connection(self, original: Callable, address, *args, **kwargs):
        host, port = address[0], address[1]
        if self._is_allowed(host, port):
            return original(address, *args, **kwargs)

        self._block(host, port)

    def getaddrinfo(self, original: Callable, host, port, *args, **kwargs):
        if self._is_allowed(host, port):
            return original(host, port, *args, **kwargs)

        self._block(host, port)

    def _is_allowed(self, host, port) -> bool:
        normalized_host = host.decode() if isinstance(host, bytes) else str(host)
        try:
            is_loopback = ipaddress.ip_address(normalized_host).is_loopback
        except ValueError:
            is_loopback = normalized_host.lower() in self.allowed_hostnames

        try:
            normalized_port = int(port)
        except (TypeError, ValueError):
            return False

        return is_loopback and normalized_port in self.allowed_ports

    def _block(self, host, port):
        destination = f"{host}:{port}"
        self.blocked_attempts.append(destination)
        raise RuntimeError(
            f"External network access is disabled during tests: {destination}"
        )


NETWORK_GUARD = NetworkGuard()


def _guarded_socket_connect(sock, address):
    return NETWORK_GUARD.connect(_ORIGINAL_SOCKET_CONNECT, sock, address)


def _guarded_socket_connect_ex(sock, address):
    return NETWORK_GUARD.connect_ex(_ORIGINAL_SOCKET_CONNECT_EX, sock, address)


def _guarded_create_connection(address, *args, **kwargs):
    return NETWORK_GUARD.create_connection(
        _ORIGINAL_CREATE_CONNECTION,
        address,
        *args,
        **kwargs,
    )


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    return NETWORK_GUARD.getaddrinfo(
        _ORIGINAL_GETADDRINFO,
        host,
        port,
        *args,
        **kwargs,
    )


def pytest_sessionstart(session):
    socket.socket.connect = _guarded_socket_connect
    socket.socket.connect_ex = _guarded_socket_connect_ex
    socket.create_connection = _guarded_create_connection
    socket.getaddrinfo = _guarded_getaddrinfo


def pytest_sessionfinish(session, exitstatus):
    socket.socket.connect = _ORIGINAL_SOCKET_CONNECT
    socket.socket.connect_ex = _ORIGINAL_SOCKET_CONNECT_EX
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    socket.getaddrinfo = _ORIGINAL_GETADDRINFO


@pytest.fixture
def network_guard():
    return NETWORK_GUARD


@pytest.fixture(autouse=True)
def isolate_test_effects(tmp_path, settings):
    from django.core.cache import caches
    from django.core.cache.backends.locmem import LocMemCache

    # The DRF throttles count in the cache (per IP for the login): each test
    # starts from zero, whatever the tests before it did. Only the in-memory
    # cache of backend.settings.ci: on Redis, clear() is a FLUSHDB that would
    # also empty the broker's queues.
    if isinstance(caches["default"], LocMemCache):
        caches["default"].clear()
    checkpoint = NETWORK_GUARD.checkpoint()
    previous_media_root = settings.MEDIA_ROOT
    isolated_media_root = tmp_path / "media"
    isolated_media_root.mkdir()
    settings.MEDIA_ROOT = Path(isolated_media_root)

    yield

    settings.MEDIA_ROOT = previous_media_root
    NETWORK_GUARD.assert_no_blocked_attempts_since(checkpoint)


# ---------------------------------------------------------------------------
# Fixtures de dominio compartidas (CORE-06)
#
# - user / workspace / wallet_factory: crear un User dispara señales que crean
#   su Workspace personal y la Wallet de ese Workspace.
# - free_plan / pro_plan: planes en memoria (PricingEngine solo lee atributos).
# - synthetic_video_path: MP4 real de 2s (video + audio) generado con FFmpeg,
#   una sola vez por sesión.
# - synthetic_clip: clip MoviePy en memoria de 2s, para tests de layouts.
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="tester", email="tester@test.com", password="password"
    )


@pytest.fixture
def workspace(user):
    """Workspace personal creado por la señal post_save de User."""
    return user.owned_workspaces.get()


@pytest.fixture
def wallet_factory(workspace):
    """Devuelve la Wallet del workspace con el saldo disponible pedido."""
    from decimal import Decimal

    def _make(available="100.00"):
        wallet = workspace.wallet
        wallet.available_balance = Decimal(available)
        wallet.reserved_balance = Decimal("0")
        wallet.save()
        return wallet

    return _make


def _subscription_plan(**fields):
    from apps.payments.models import SubscriptionPlan

    return SubscriptionPlan(**fields)


@pytest.fixture
def free_plan():
    from decimal import Decimal

    return _subscription_plan(
        name="Free",
        max_video_duration_seconds=60,
        max_resolution="720p",
        has_watermark=True,
        has_seo_optimization=False,
        has_thumbnail_engine=False,
        base_discount_rate=Decimal("0.00"),
    )


@pytest.fixture
def pro_plan():
    from decimal import Decimal

    return _subscription_plan(
        name="Pro",
        max_video_duration_seconds=600,
        max_resolution="4K",
        has_watermark=False,
        has_seo_optimization=True,
        has_thumbnail_engine=True,
        base_discount_rate=Decimal("0.20"),
    )


def _ffmpeg_exe():
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg  # viene con moviepy

    return imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture(scope="session")
def synthetic_video_path(tmp_path_factory):
    """MP4 de 2s, 640x360, 24fps, con tono de audio. Se genera una vez por sesión."""
    import subprocess

    path = tmp_path_factory.mktemp("media_src") / "synthetic_2s.mp4"
    subprocess.run(
        [
            _ffmpeg_exe(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture
def synthetic_clip():
    """Clip MoviePy en memoria: 2s, 640x360 (16:9), con un gradiente que se mueve."""
    import numpy as np
    from moviepy import VideoClip

    w, h = 640, 360
    xs = np.linspace(0, 255, w, dtype=np.uint8)

    def make_frame(t):
        row = np.roll(xs, int(t * 100) % w)
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :, 0] = row
        frame[:, :, 1] = 128
        frame[:, :, 2] = 255 - row
        return frame

    clip = VideoClip(make_frame, duration=2).with_fps(12)
    yield clip
    clip.close()
