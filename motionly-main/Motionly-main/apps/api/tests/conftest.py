import ipaddress
import os
import shutil
import socket
import tempfile

import pytest

# Test collection imports app.main before fixtures run. Override repository and
# inherited shell defaults so collection is hermetic and authentication fails
# closed. Individual tests can still opt in with monkeypatch after collection.
os.environ["APP_ENV"] = "local"
os.environ["SKIP_API_AUTH"] = "false"

from app.core.config import get_settings  # noqa: E402

RUN_GPU_STAGE_TESTS = os.getenv("RUN_GPU_STAGE_TESTS") == "1"

# A live provider run (Task 17's manual contract test) is the only exemption
# from the no-network guard, and only when it is explicitly authorised.
LIVE_INFERENCE_ENABLED = os.getenv("MOTIONLY_LIVE_INFERENCE") == "1"


# ---------------------------------------------------------------------------
# No-network CI contract (plan Section 0.2 invariant 9, Task 1)
# ---------------------------------------------------------------------------
# Installed at import time so collection is guarded too. Loopback and AF_UNIX
# are allowed because the local Supabase/Redis contract stages use 127.0.0.1
# (scripts/verify/stages.json). Any non-loopback connect or any DNS resolution
# of a non-local hostname fails immediately, naming the target.


class NetworkBlockedError(RuntimeError):
    """A test tried to reach a non-loopback network endpoint under the guard."""


_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

# RFC 2606 / RFC 6761 documentation domains used across the suite as stand-ins
# for "a valid public host" (e.g. https://cdn.example.com/... in render manifest
# validation). They are resolved offline to a canned public address so SSRF
# validation stays exercised without a real DNS query. The ``.invalid``/``.test``
# reserved names are intentionally NOT resolved: tests rely on them failing.
_DOCUMENTATION_SUFFIXES = (".example.com", ".example.net", ".example.org")
_DOCUMENTATION_HOSTS = frozenset({"example.com", "example.net", "example.org"})
# Canonical public address of example.com; never actually contacted.
_DOCUMENTATION_PUBLIC_IP = "93.184.216.34"


def _is_documentation_host(name: str) -> bool:
    return name in _DOCUMENTATION_HOSTS or name.endswith(_DOCUMENTATION_SUFFIXES)


def _is_loopback_host(host: str) -> bool:
    name = str(host).strip().strip("[]").lower()
    if not name or name in _LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _guard_address(address: object) -> None:
    if not isinstance(address, tuple) or not address:
        return
    host = address[0]
    if host is None or _is_loopback_host(str(host)):
        return
    raise NetworkBlockedError(
        f"Outbound network blocked by the test guard: refused connection to {host!r}. "
        "Use a fake adapter/server, or mark the test @pytest.mark.live_provider "
        "and set MOTIONLY_LIVE_INFERENCE=1 for an authorised live run."
    )


def _offline_address_for(host: object) -> str | None:
    """Return a canned address for a documentation host, ``None`` for a local
    host (resolve normally), and raise for any other name."""
    name = str(host).strip().strip("[]").lower()
    if _is_loopback_host(name):
        return None
    if _is_documentation_host(name):
        return _DOCUMENTATION_PUBLIC_IP
    raise NetworkBlockedError(
        f"DNS resolution blocked by the test guard: refused lookup of {host!r}. "
        "Use a fake adapter/server, or mark the test @pytest.mark.live_provider "
        "and set MOTIONLY_LIVE_INFERENCE=1 for an authorised live run."
    )


def _install_network_guard() -> None:
    if LIVE_INFERENCE_ENABLED:
        return
    af_unix = getattr(socket, "AF_UNIX", None)
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex

    def guarded_connect(self, address, *args, **kwargs):  # noqa: ANN001
        if self.family != af_unix:
            _guard_address(address)
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):  # noqa: ANN001
        if self.family != af_unix:
            _guard_address(address)
        return real_connect_ex(self, address, *args, **kwargs)

    # Connectionless (UDP) sends name their target per call, so connect() alone
    # does not cover them. sendto(data, address) / sendto(data, flags, address).
    def guarded_sendto(self, data, *args):  # noqa: ANN001
        if self.family != af_unix and args:
            _guard_address(args[-1])
        return real_sendto(self, data, *args)

    def guarded_sendmsg(self, buffers, *args):  # noqa: ANN001
        # sendmsg(buffers[, ancdata[, flags[, address]]])
        if self.family != af_unix and len(args) >= 3:
            _guard_address(args[2])
        return real_sendmsg(self, buffers, *args)

    def guarded_create_connection(address, *args, **kwargs):  # noqa: ANN001
        _guard_address(address)
        return real_create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
        if host is not None:
            offline = _offline_address_for(host)
            if offline is not None:
                # Offline synthetic record; no real DNS query is made.
                port = args[0] if args else kwargs.get("port", 0)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (offline, port or 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    # gethostbyname* call the C resolver directly, bypassing getaddrinfo.
    def guarded_gethostbyname(host):  # noqa: ANN001
        offline = _offline_address_for(host)
        return offline if offline is not None else real_gethostbyname(host)

    def guarded_gethostbyname_ex(host):  # noqa: ANN001
        offline = _offline_address_for(host)
        if offline is not None:
            return (str(host), [], [offline])
        return real_gethostbyname_ex(host)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = guarded_sendto  # type: ignore[method-assign]
    if real_sendmsg is not None:
        socket.socket.sendmsg = guarded_sendmsg  # type: ignore[method-assign]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = guarded_gethostbyname  # type: ignore[assignment]
    socket.gethostbyname_ex = guarded_gethostbyname_ex  # type: ignore[assignment]


_install_network_guard()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip GPU-stage and unauthorised live-provider tests.

    The GPU-stage skip reason carries the manifest id so
    scripts/verify/skip_audit.py can prove every deferred check is declared in
    tests/gpu-stage.json. A ``live_provider`` test is skipped unless an operator
    sets MOTIONLY_LIVE_INFERENCE=1 (the only case the network guard stands down).
    """
    for item in items:
        if item.get_closest_marker("live_provider") is not None and not LIVE_INFERENCE_ENABLED:
            item.add_marker(
                pytest.mark.skip(
                    reason="live_provider: set MOTIONLY_LIVE_INFERENCE=1 for an "
                    "authorised live run (network guard stands down only then)"
                )
            )
        marker = item.get_closest_marker("gpu_stage")
        if marker is None:
            continue
        gpu_stage_id = marker.args[0] if marker.args else marker.kwargs.get("id")
        if not gpu_stage_id:
            raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.gpu_stage needs a manifest id")
        if not RUN_GPU_STAGE_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"gpu-stage[{gpu_stage_id}]: deferred until the GPU stage is approved"
                )
            )


@pytest.fixture(scope="session", autouse=True)
def contain_temporary_files(tmp_path_factory: pytest.TempPathFactory):
    """Files tests create with ``tempfile`` go to one session directory that is
    removed afterwards, instead of accumulating in the system temp directory."""
    previous = tempfile.tempdir
    directory = tmp_path_factory.mktemp("tempfile")
    tempfile.tempdir = str(directory)
    yield
    tempfile.tempdir = previous
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def default_local_app_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
