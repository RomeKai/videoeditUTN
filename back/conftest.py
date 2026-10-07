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


@dataclass
class NetworkGuard:
    """Blocks test-process network traffic except local PostgreSQL."""

    blocked_attempts: list[str] = field(default_factory=list)
    allowed_hostnames = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})
    allowed_ports = frozenset({5432})

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
    checkpoint = NETWORK_GUARD.checkpoint()
    previous_media_root = settings.MEDIA_ROOT
    isolated_media_root = tmp_path / "media"
    isolated_media_root.mkdir()
    settings.MEDIA_ROOT = Path(isolated_media_root)

    yield

    settings.MEDIA_ROOT = previous_media_root
    NETWORK_GUARD.assert_no_blocked_attempts_since(checkpoint)
