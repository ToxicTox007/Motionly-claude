"""The no-network CI contract (Task 1): non-loopback traffic is blocked.

The guard is installed in conftest at import time. These tests confirm that a
connection or DNS lookup to a non-loopback target fails immediately with a clear
error naming the target, that loopback traffic is allowed (a real 127.0.0.1
listener is reachable), and that a live_provider-marked test is skipped without
MOTIONLY_LIVE_INFERENCE=1.
"""

from __future__ import annotations

import socket

import pytest

from tests.conftest import NetworkBlockedError

# TEST-NET-3 documentation range (RFC 5737): never routable, safe to name.
_BLOCKED_IP = "203.0.113.1"
_BLOCKED_HOST = "example.invalid"


def test_non_loopback_connect_is_blocked_with_target_in_message() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError) as excinfo:
            sock.connect((_BLOCKED_IP, 443))
        assert _BLOCKED_IP in str(excinfo.value)
    finally:
        sock.close()


def test_non_loopback_create_connection_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError) as excinfo:
        socket.create_connection((_BLOCKED_IP, 443), timeout=0.1)
    assert _BLOCKED_IP in str(excinfo.value)


def test_public_hostname_dns_resolution_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError) as excinfo:
        socket.getaddrinfo(_BLOCKED_HOST, 443)
    assert _BLOCKED_HOST in str(excinfo.value)


def test_non_loopback_ipv6_connect_is_blocked() -> None:
    if not socket.has_ipv6:
        pytest.skip("IPv6 sockets unavailable")
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        # 2001:db8::/32 is the IPv6 documentation range (RFC 3849).
        with pytest.raises(NetworkBlockedError):
            sock.connect(("2001:db8::1", 443, 0, 0))
    finally:
        sock.close()


def test_non_loopback_udp_sendto_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"x", (_BLOCKED_IP, 53))
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"x", 0, (_BLOCKED_IP, 53))
        with pytest.raises(NetworkBlockedError):
            sock.sendmsg([b"x"], [], 0, (_BLOCKED_IP, 53))
    finally:
        sock.close()


def test_gethostbyname_is_guarded() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyname(_BLOCKED_HOST)
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyname_ex(_BLOCKED_HOST)
    assert socket.gethostbyname("localhost")


def test_loopback_udp_sendto_is_allowed() -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1.0)
        sender.sendto(b"ping", receiver.getsockname())
        assert receiver.recv(16) == b"ping"
    finally:
        sender.close()
        receiver.close()


def test_loopback_dns_resolution_is_allowed() -> None:
    # Must not raise; resolving localhost/127.0.0.1 stays hermetic.
    results = socket.getaddrinfo("127.0.0.1", 0)
    assert results
    assert socket.getaddrinfo("localhost", 0)


def test_loopback_connect_is_allowed() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.settimeout(1.0)
        client.connect(("127.0.0.1", port))
        conn, _ = listener.accept()
        conn.close()
    finally:
        client.close()
        listener.close()


@pytest.mark.live_provider
def test_live_provider_is_skipped_without_env_var() -> None:
    # This body must never run in a normal (no MOTIONLY_LIVE_INFERENCE) suite;
    # the marker causes a skip during collection. If it ran, it would fail.
    raise AssertionError("live_provider test ran without MOTIONLY_LIVE_INFERENCE=1")
