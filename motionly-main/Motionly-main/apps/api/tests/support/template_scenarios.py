"""Shared harness for the Task 17 template ports (object detection, diarization,
stem separation).

Three parallel capability agents port one capability each on top of this module;
it is the single place that knows how to turn a JSON *scenario* into a running
template route and a normalized :class:`Observation`. The conformance suite
(``test_inference_template_conformance.py``), the differential suite
(``test_inference_template_differential.py``) and the manual live suite
(``tests/live/test_live_template_routes.py``) all build on the pieces here.

Nothing in this module names a vendor. Vendor names live only in operator-shaped
example *registry documents* (``model_id``/``base_url``/``provider``/
``data_processor``/``mapping.artifacts.allowed_hosts`` fields, where the Task 2
scanner allows them) and in the archived adapter modules the capability agents
add under ``tests/differential/``.

Layout on disk
--------------
* Registry example documents: ``docs/ai-registry/examples/*.json`` (one or more
  per capability; the second, differently shaped provider lives here too, e.g.
  ``object-detection-alt.json``).
* Scenario files: ``docs/ai-registry/examples/scenarios/<capability-id>/<name>.json``
  where ``<capability-id>`` is the literal fine-grained id,
  e.g. ``vision.object_detection``.
* Scenario media: ``docs/ai-registry/examples/scenarios/<capability-id>/media/``.

A smoke scenario and its registry live under
``apps/api/tests/fixtures/template_scenarios/`` so this harness is exercised now,
before any capability agent adds files.

Scenario JSON schema (field by field)
--------------------------------------
Top-level object:

* ``registry`` (str, required): file name of the example registry document the
  scenario loads, resolved relative to the scenario file's directory and then to
  ``docs/ai-registry/examples/``. The route is built from this document with
  :func:`app.services.inference_runtime.build_inference_runtime`.
* ``route_id`` (str, required): the registry route id to exercise.
* ``capability`` (str, optional): the fine-grained capability id. Defaults to the
  scenario's parent directory name. Must match the route's capability.
* ``request`` (object, required): a typed request spec. ``type`` selects the
  builder (``object_detection`` | ``diarization`` | ``stem_separation`` |
  ``generic_http`` for the smoke example). Fields by type:
  - ``object_detection``: ``images`` (list of media refs; see below),
    ``max_results`` (int, optional).
  - ``diarization``: ``audio`` (media ref), ``duration_s`` (number),
    ``minimum_billable_s`` (int, optional).
  - ``stem_separation``: ``source`` (media ref), ``model`` (str),
    ``duration_s`` (number, optional), ``stems`` (list of requested stem names,
    optional, informational).
  A *media ref* is either ``{"file": "<name>"}`` (a file under the scenario's
  ``media/`` directory) or ``{"b64": "<base64>"}`` (tiny inline bytes).
* ``exchanges`` (list, required for a template run): the fake provider. Each
  entry is ``{"match": {...}, "response": {...}, "repeat": <int|"always">}``:
  - ``match``: ``method`` (str), ``host`` (str, optional logical host; the base
    URL host when omitted), and one of ``path`` (exact) or ``path_prefix``.
  - ``response``: ``status`` (int), one of ``json`` (object/list), ``body_b64``
    (base64 bytes) or ``body_file`` (a file under ``media/``); ``headers``
    (object, optional).
  - ``repeat``: how many times this exchange may serve before the next matching
    one takes over. Defaults to ``1``; ``"always"`` serves indefinitely (used by
    a poll that returns the same terminal status). Exchanges are matched in order
    and an exhausted one is skipped.
* ``expect`` (object, optional): any of
  - ``output``: the normalized typed output as JSON (see
    :func:`_output_to_json`).
  - ``usage``: ``{"input_units": <int>, ...}``.
  - ``error``: ``{"code": <str>, "fallback_eligible": <bool>,
    "billable": <bool>}``.
  - ``requests``: a list of outgoing request shapes, each
    ``{"method", "host", "path", "header_names": [...], "body_keys": [...] |
    "body": {...}}``. Absent keys are not checked.
  - ``cleanup``: a list of expected cleanup request shapes (same shape as
    ``requests``; the harness marks a request as cleanup when its path matches a
    mapping ``cleanup`` entry).
  - ``operation``: ``{"state": <str>, "provider_operation_id": <str|null>}`` —
    the final ``inference_operations`` row for an async route.
* ``differential`` (bool, optional, default ``true``): whether the differential
  suite pairs this scenario with the archived adapter.
* ``differential_skip_reason`` (str, optional): why ``differential`` is ``false``
  (for example a second provider shape the archived adapter cannot run).

See ``docs/ai-registry/examples/README.md`` for a complete minimal example.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES_DIR = REPO_ROOT / "docs" / "ai-registry" / "examples"
SCENARIOS_DIR = EXAMPLES_DIR / "scenarios"
SMOKE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "template_scenarios"

# The three ported capabilities and their archived-module slugs (Task 17).
CAPABILITY_SLUGS: Mapping[str, str] = {
    "vision.object_detection": "objects",
    "audio.diarization": "diarization",
    "audio.stem_separation": "stems",
}

# A sentinel secret value: the bundle resolves every ref to this, so a leak is
# unambiguous in a log scan. Never a real credential.
SENTINEL_SECRET = "sentinel-secret-value-do-not-log"  # noqa: S105 - test sentinel


class ScenarioError(RuntimeError):
    """A scenario file is malformed or references a missing file."""


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OutgoingRequest:
    """One normalized outgoing HTTP request the harness observed.

    * ``method`` upper-cased.
    * ``host`` the *logical* host (the Host header the server sees, which for a
      pinned ``auth: none`` request is the captured URL host, not the socket
      address).
    * ``path`` the URL path (no query).
    * ``header_names`` the lower-cased header names present, sorted; header
      *values* are never recorded (they may carry a secret).
    * ``body_keys`` the top-level JSON body keys, sorted (``()`` for a raw
      upload or an empty body).
    * ``body`` the full decoded JSON body when it is a small object, else
      ``None``. The differential/compare path normalizes random ids out of it.
    * ``is_upload`` true for a raw ``application/octet-stream`` upload.
    * ``is_cleanup`` true when the path matched a mapping ``cleanup`` entry.
    """

    method: str
    host: str
    path: str
    header_names: tuple[str, ...]
    body_keys: tuple[str, ...]
    body: Mapping[str, Any] | None = None
    is_upload: bool = False
    is_cleanup: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": self.method,
            "host": self.host,
            "path": self.path,
            "header_names": list(self.header_names),
            "body_keys": list(self.body_keys),
            "is_upload": self.is_upload,
            "is_cleanup": self.is_cleanup,
        }
        if self.body is not None:
            out["body"] = dict(self.body)
        return out


@dataclass(frozen=True)
class ObservedError:
    """The provider error class the call raised (parity dimension)."""

    code: str
    fallback_eligible: bool
    billable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "fallback_eligible": self.fallback_eligible,
            "billable": self.billable,
        }


@dataclass(frozen=True)
class Observation:
    """The comparable result of running one scenario through one side.

    ``requests`` is the ordered outgoing-request sequence. ``output`` is the
    normalized typed output as JSON (``None`` on error). ``usage`` is the usage
    dict (``{"input_units": int}``). ``error`` is the provider error class on
    failure, else ``None``. ``cleanup`` is the subsequence of ``requests`` marked
    cleanup. ``operation`` is the final ``inference_operations`` state for an
    async route (``None`` for a generic_http route).

    Normalization rules (applied by :func:`compare_observations`, documented on
    it): route ids, revision strings and random identifiers (uuids, media names,
    idempotency keys) are ignored so two differently-identified sides compare
    equal on behavior.
    """

    requests: tuple[OutgoingRequest, ...] = ()
    output: Any | None = None
    usage: Mapping[str, Any] | None = None
    error: ObservedError | None = None
    operation: Mapping[str, Any] | None = None

    @property
    def cleanup(self) -> tuple[OutgoingRequest, ...]:
        return tuple(r for r in self.requests if r.is_cleanup)

    def to_json(self) -> dict[str, Any]:
        return {
            "requests": [r.to_json() for r in self.requests],
            "output": self.output,
            "usage": dict(self.usage) if self.usage is not None else None,
            "error": self.error.to_json() if self.error is not None else None,
            "cleanup": [r.to_json() for r in self.cleanup],
            "operation": dict(self.operation) if self.operation is not None else None,
        }


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scenario:
    """A parsed scenario plus the resolved paths it needs."""

    path: Path
    data: Mapping[str, Any]

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def capability(self) -> str:
        cap = self.data.get("capability")
        if isinstance(cap, str) and cap:
            return cap
        # Default: the parent directory name is the literal capability id.
        return self.directory.name

    @property
    def route_id(self) -> str:
        route_id = self.data.get("route_id")
        if not isinstance(route_id, str) or not route_id:
            raise ScenarioError(f"{self.path}: 'route_id' is required")
        return route_id

    @property
    def registry_path(self) -> Path:
        name = self.data.get("registry")
        if not isinstance(name, str) or not name:
            raise ScenarioError(f"{self.path}: 'registry' is required")
        candidates = [self.directory / name, EXAMPLES_DIR / name, Path(name)]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise ScenarioError(f"{self.path}: registry {name!r} not found")

    @property
    def is_differential(self) -> bool:
        return bool(self.data.get("differential", True))

    @property
    def differential_skip_reason(self) -> str | None:
        reason = self.data.get("differential_skip_reason")
        return reason if isinstance(reason, str) else None

    def media_bytes(self, ref: Mapping[str, Any]) -> bytes:
        """Resolve a media ref (``{"file": ...}`` or ``{"b64": ...}``) to bytes."""
        if "b64" in ref:
            return base64.b64decode(str(ref["b64"]))
        if "file" in ref:
            media = self.directory / "media" / str(ref["file"])
            if not media.is_file():
                raise ScenarioError(f"{self.path}: media file {ref['file']!r} not found")
            return media.read_bytes()
        raise ScenarioError(f"{self.path}: media ref needs 'file' or 'b64': {ref!r}")


def load_scenario(scenario_path: str | Path) -> Scenario:
    path = Path(scenario_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"{path}: could not load scenario: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ScenarioError(f"{path}: scenario must be a JSON object")
    return Scenario(path=path, data=data)


def discover_scenarios(capability: str | None = None) -> list[Path]:
    """Every scenario file under ``docs/ai-registry/examples/scenarios`` by glob.

    When ``capability`` is given, only that capability's directory is searched.
    The smoke scenarios under ``apps/api/tests/fixtures/template_scenarios`` are
    discovered separately by :func:`discover_smoke_scenarios`.
    """
    root = SCENARIOS_DIR / capability if capability else SCENARIOS_DIR
    if not root.exists():
        return []
    return sorted(
        p
        for p in root.rglob("*.json")
        if p.is_file() and not p.name.endswith(".registry.json")
    )


def discover_smoke_scenarios() -> list[Path]:
    if not SMOKE_DIR.exists():
        return []
    return sorted(
        p
        for p in SMOKE_DIR.rglob("*.json")
        if p.is_file() and not p.name.endswith(".registry.json")
    )


def capabilities_with_scenarios() -> list[str]:
    if not SCENARIOS_DIR.exists():
        return []
    return sorted(
        p.name for p in SCENARIOS_DIR.iterdir() if p.is_dir() and any(p.glob("*.json"))
    )


# ---------------------------------------------------------------------------
# Secret bundle (sentinel values)
# ---------------------------------------------------------------------------
def write_sentinel_bundle(registry_path: Path, tmp_path: Path) -> Path:
    """Write a bundle file whose every referenced key resolves to the sentinel.

    Reads the registry document for ``auth.secret_ref`` values of the form
    ``bundle:<key>`` / ``file:<key>`` and maps every ``bundle:`` key to the
    sentinel. A ``file:`` ref is materialized under ``tmp_path/secrets``.
    """
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    bundle: dict[str, str] = {}
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    for adapter in document.get("adapters", []):
        auth = adapter.get("auth") or {}
        for key in ("secret_ref", "client_secret_ref"):
            ref = auth.get(key)
            if not isinstance(ref, str) or ":" not in ref:
                continue
            scheme, _, rest = ref.partition(":")
            if scheme == "bundle":
                bundle[rest] = SENTINEL_SECRET
            elif scheme == "file":
                (secrets_dir / rest).write_text(SENTINEL_SECRET, encoding="utf-8")
    bundle_path = tmp_path / "secret-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return bundle_path


# ---------------------------------------------------------------------------
# Fake provider transport driven by ``exchanges``
# ---------------------------------------------------------------------------
class _Exchange:
    def __init__(self, raw: Mapping[str, Any], scenario: Scenario, base_host: str) -> None:
        match = raw.get("match") or {}
        self.method = str(match.get("method", "")).upper()
        self.host = str(match.get("host")) if match.get("host") else None
        self.path = match.get("path")
        self.path_prefix = match.get("path_prefix")
        response = raw.get("response") or {}
        self.status = int(response.get("status", 200))
        self.json = response.get("json")
        self.body_file = response.get("body_file")
        self.body_b64 = response.get("body_b64")
        self.headers = dict(response.get("headers") or {})
        repeat = raw.get("repeat", 1)
        self.unlimited = repeat == "always"
        self.remaining = 10**9 if self.unlimited else int(repeat)
        self._scenario = scenario
        self._base_host = base_host

    def matches(self, request: httpx.Request, logical_host: str) -> bool:
        if self.remaining <= 0:
            return False
        if self.method and request.method.upper() != self.method:
            return False
        if self.host is not None and logical_host != self.host:
            return False
        path = request.url.path
        if self.path is not None and path != self.path:
            return False
        if self.path_prefix is not None and not path.startswith(str(self.path_prefix)):
            return False
        return True

    def build(self, request: httpx.Request) -> httpx.Response:
        if not self.unlimited:
            self.remaining -= 1
        if self.body_file is not None:
            data = (self._scenario.directory / "media" / str(self.body_file)).read_bytes()
            return httpx.Response(
                self.status, content=data, headers=self.headers, request=request
            )
        if self.body_b64 is not None:
            data = base64.b64decode(str(self.body_b64))
            return httpx.Response(
                self.status, content=data, headers=self.headers, request=request
            )
        if self.json is not None:
            return httpx.Response(
                self.status, json=self.json, headers=self.headers, request=request
            )
        return httpx.Response(self.status, headers=self.headers, request=request)


class _RecordingServer:
    """An ``httpx`` handler that serves ``exchanges`` and records requests.

    Mirrors ``tests/test_async_job_adapter.py``: an ``auth: none`` request is
    pinned to a validated IP, and the logical host travels in the Host header, so
    the handler reads the Host header to recover the logical host.
    """

    def __init__(self, scenario: Scenario, base_host: str) -> None:
        self._exchanges = [
            _Exchange(raw, scenario, base_host)
            for raw in (scenario.data.get("exchanges") or [])
        ]
        self._base_host = base_host
        self.records: list[tuple[httpx.Request, str, bool]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        logical_host = request.headers.get("host", request.url.host).split(":")[0]
        is_upload = "application/octet-stream" in request.headers.get("content-type", "")
        self.records.append((request, logical_host, is_upload))
        for exchange in self._exchanges:
            if exchange.matches(request, logical_host):
                return exchange.build(request)
        return httpx.Response(
            404,
            json={"error": "no matching exchange", "path": request.url.path},
            request=request,
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _public_resolver(host: str, port: int, **_kwargs: Any):
    """A fake DNS resolver that keeps every host public (no sockets).

    The same canned public address the async-adapter tests use, so a pinned
    ``auth: none`` upload or artifact download connects to a validated address.
    """
    import socket

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class _FakeClock:
    """A monotonic clock that advances only when ``sleep`` is called."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


# ---------------------------------------------------------------------------
# Request builders (one per request type)
# ---------------------------------------------------------------------------
def build_object_detection_request(scenario: Scenario):
    from app.services.model_gateway import ObjectDetectionRequest

    spec = scenario.data["request"]
    images = tuple(scenario.media_bytes(ref) for ref in spec.get("images", []))
    kwargs: dict[str, Any] = {"images": images}
    if "max_results" in spec:
        kwargs["max_results"] = int(spec["max_results"])
    return ObjectDetectionRequest(**kwargs)


def build_diarization_request(scenario: Scenario, tmp_path: Path):
    from app.services.model_gateway import DiarizationRequest

    spec = scenario.data["request"]
    audio_path = tmp_path / "diarization-input.bin"
    audio_path.write_bytes(scenario.media_bytes(spec["audio"]))
    kwargs: dict[str, Any] = {
        "audio_path": audio_path,
        "duration_s": float(spec["duration_s"]),
    }
    if "minimum_billable_s" in spec:
        kwargs["minimum_billable_s"] = int(spec["minimum_billable_s"])
    return DiarizationRequest(**kwargs)


def build_stem_separation_request(scenario: Scenario, tmp_path: Path):
    from app.services.model_gateway import StemSeparationRequest

    spec = scenario.data["request"]
    source_path = tmp_path / "stem-input.bin"
    source_path.write_bytes(scenario.media_bytes(spec["source"]))
    kwargs: dict[str, Any] = {
        "source_path": source_path,
        "model": str(spec.get("model", "stem-model")),
    }
    if "duration_s" in spec:
        kwargs["duration_s"] = float(spec["duration_s"])
    return StemSeparationRequest(**kwargs)


def build_request(scenario: Scenario, tmp_path: Path):
    """Build the typed request for a scenario (dispatch on ``request.type``)."""
    request_type = str(scenario.data.get("request", {}).get("type", ""))
    if request_type == "object_detection":
        return build_object_detection_request(scenario)
    if request_type == "diarization":
        return build_diarization_request(scenario, tmp_path)
    if request_type == "stem_separation":
        return build_stem_separation_request(scenario, tmp_path)
    if request_type == "generic_http":
        # The smoke example reuses the object_detection request shape.
        return build_object_detection_request(scenario)
    raise ScenarioError(f"{scenario.path}: unknown request type {request_type!r}")


# ---------------------------------------------------------------------------
# Output normalization (typed output -> JSON)
# ---------------------------------------------------------------------------
def _output_to_json(output: Any) -> Any:
    """Serialize a typed gateway output dataclass to comparable JSON.

    Object detection: ``{"detections": [[{label, score, box:[x,y,w,h]}, ...], ...]}``
    with scores/boxes rounded to 6 places. Diarization:
    ``{"segments": [{speaker, start_ms, end_ms}, ...]}``. Stems:
    ``{"stems": {name: size_bytes}}`` (paths are per-run temp files, so only the
    stem names and byte sizes are comparable).
    """
    from app.services.model_gateway import (
        DiarizationOutput,
        ObjectDetectionOutput,
        StemSeparationOutput,
    )

    if isinstance(output, ObjectDetectionOutput):
        return {
            "detections": [
                [
                    {
                        "label": d.label,
                        "score": round(d.confidence, 6),
                        "box": [
                            round(d.x, 6),
                            round(d.y, 6),
                            round(d.width, 6),
                            round(d.height, 6),
                        ],
                    }
                    for d in image
                ]
                for image in output.detections
            ]
        }
    if isinstance(output, DiarizationOutput):
        return {
            "segments": [
                {"speaker": s.speaker, "start_ms": s.start_ms, "end_ms": s.end_ms}
                for s in output.segments
            ]
        }
    if isinstance(output, StemSeparationOutput):
        return {
            "stems": {
                name: (path.stat().st_size if path.exists() else 0)
                for name, path in sorted(output.stems.items())
            }
        }
    raise ScenarioError(f"unsupported output type {type(output).__name__}")


# ---------------------------------------------------------------------------
# Adapter recording wrapper (captures the raised provider error)
# ---------------------------------------------------------------------------
class _RecordingAdapter:
    """Wraps an adapter so the harness sees the raw ``ProviderCallFailure``.

    The gateway swallows a provider failure into ``ModelProvidersExhausted`` with
    only ``{route_id, code}``, but the Observation needs the full error class
    (``fallback_eligible``/``billable``). This wrapper re-raises the original
    failure (the gateway still records it) after stashing its ``error_info``.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.error_info: Any | None = None

    def invoke(self, *, route: Any, request: Any):
        from app.services.provider_resilience import ProviderCallFailure

        try:
            return self._inner.invoke(route=route, request=request)
        except ProviderCallFailure as exc:
            self.error_info = exc.error_info
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Runtime construction + run
# ---------------------------------------------------------------------------
def _fake_audio_runner(command: list[str]):
    import subprocess

    output = Path(command[-1])
    output.write_bytes(b"FLACfake-prepared-audio")
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _patch_async_adapters(runtime: Any, clock: _FakeClock) -> None:
    """Inject the fake clock/sleep and public DNS resolver into async adapters.

    ``build_inference_runtime`` constructs an :class:`AsyncJobAdapter` without
    clock/sleep/resolver (they default to the real ones). For a hermetic,
    socket-free run the harness replaces them on the built adapter, exactly the
    knobs ``tests/test_async_job_adapter.py`` injects at construction.
    """
    from app.services.async_job_adapter import AsyncJobAdapter

    for adapter in list(runtime.gateway._adapters.values()):  # noqa: SLF001
        target = adapter._inner if isinstance(adapter, _RecordingAdapter) else adapter
        if isinstance(target, AsyncJobAdapter):
            target._sleep = clock.sleep  # noqa: SLF001
            target._clock = clock  # noqa: SLF001
            target._dns_resolver = _public_resolver  # noqa: SLF001
            target._audio_runner = _fake_audio_runner  # noqa: SLF001


def _wrap_adapters(runtime: Any) -> dict[str, _RecordingAdapter]:
    wrapped: dict[str, _RecordingAdapter] = {}
    for key, adapter in list(runtime.gateway._adapters.items()):  # noqa: SLF001
        recording = _RecordingAdapter(adapter)
        runtime.gateway._adapters[key] = recording  # noqa: SLF001
        wrapped[key] = recording
    return wrapped


def _build_runtime(scenario: Scenario, tmp_path: Path, server: _RecordingServer):
    from app.core.config import get_settings
    from app.services.inference_operations import InMemoryInferenceOperationRepository
    from app.services.inference_runtime import build_inference_runtime
    from app.services.inference_secrets import SecretResolver

    bundle_path = write_sentinel_bundle(scenario.registry_path, tmp_path)
    settings = get_settings().model_copy(
        update={
            "ai_registry_file": str(scenario.registry_path),
            "ai_secrets_bundle_file": str(bundle_path),
            "ai_secrets_dir": str(tmp_path / "secrets"),
            "redis_url": None,
        }
    )
    resolver = SecretResolver(settings=settings)
    operations = InMemoryInferenceOperationRepository()
    runtime = build_inference_runtime(
        settings,
        operations_repository=operations,
        secret_resolver=resolver,
        transport=server.transport(),
    )
    return runtime, operations


def _base_host_for_route(scenario: Scenario) -> str:
    document = json.loads(scenario.registry_path.read_text(encoding="utf-8"))
    route = next((r for r in document["routes"] if r["id"] == scenario.route_id), None)
    if route is None:
        raise ScenarioError(f"{scenario.path}: route {scenario.route_id!r} not in registry")
    adapter = next(a for a in document["adapters"] if a["id"] == route["adapter_id"])
    return httpx.URL(adapter["base_url"]).host


def _cleanup_paths(scenario: Scenario) -> set[str]:
    """Static path prefixes of the mapping's ``cleanup`` requests, if any."""
    document = json.loads(scenario.registry_path.read_text(encoding="utf-8"))
    route = next((r for r in document["routes"] if r["id"] == scenario.route_id), {})
    mapping = route.get("mapping") or {}
    paths: set[str] = set()
    for request in mapping.get("cleanup") or []:
        path = request.get("path")
        if isinstance(path, str):
            # Strip a trailing templated segment so '/delete/{vars.x}' matches.
            paths.add(path.split("{", 1)[0].rstrip("/") or path)
    return paths


def _records_to_requests(
    records: Sequence[tuple[httpx.Request, str, bool]], cleanup_paths: set[str]
) -> tuple[OutgoingRequest, ...]:
    out: list[OutgoingRequest] = []
    for request, logical_host, is_upload in records:
        body_keys: tuple[str, ...] = ()
        body: Mapping[str, Any] | None = None
        if not is_upload and request.content:
            try:
                parsed = json.loads(request.content)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                body_keys = tuple(sorted(str(k) for k in parsed.keys()))
                if len(parsed) <= 12:
                    body = dict(parsed)
        path = request.url.path
        is_cleanup = any(path == p or path.startswith(p + "/") for p in cleanup_paths)
        out.append(
            OutgoingRequest(
                method=request.method.upper(),
                host=logical_host,
                path=path,
                header_names=tuple(sorted(k.lower() for k in request.headers.keys())),
                body_keys=body_keys,
                body=body,
                is_upload=is_upload,
                is_cleanup=is_cleanup,
            )
        )
    return tuple(out)


def run_template_scenario(scenario_path: str | Path, *, tmp_path: Path) -> Observation:
    """Build the template route from the example registry and run the request.

    Uses :func:`build_inference_runtime` with the example registry, secret refs
    resolved from a sentinel bundle, a public fake DNS resolver, an ``httpx``
    MockTransport that serves ``exchanges`` (handling the pinned-IP Host header
    like ``tests/test_async_job_adapter.py``), and a fake clock/sleep. The typed
    request is invoked through the REAL gateway. Returns an :class:`Observation`.
    """
    from app.services.model_gateway import ModelCallContext, ModelGatewayError
    from app.services.provider_resilience import ProviderCallFailure

    scenario = load_scenario(scenario_path)
    base_host = _base_host_for_route(scenario)
    server = _RecordingServer(scenario, base_host)
    runtime, operations = _build_runtime(scenario, tmp_path, server)

    clock = _FakeClock()
    wrapped = _wrap_adapters(runtime)
    _patch_async_adapters(runtime, clock)

    request = build_request(scenario, tmp_path)
    context = ModelCallContext(idempotency_key=f"harness-{scenario.route_id}")

    output_json: Any | None = None
    usage: Mapping[str, Any] | None = None
    error: ObservedError | None = None
    try:
        result = runtime.gateway.invoke(request, context=context)
        output_json = _output_to_json(result.output)
        usage = {
            "input_units": result.metadata.usage.input_units,
            "output_units": result.metadata.usage.output_units,
        }
    except (ProviderCallFailure, ModelGatewayError) as exc:
        error = _error_from_exception(exc, wrapped)

    cleanup_paths = _cleanup_paths(scenario)
    requests = _records_to_requests(server.records, cleanup_paths)
    operation = _final_operation(operations)
    return Observation(
        requests=requests,
        output=output_json,
        usage=usage,
        error=error,
        operation=operation,
    )


def _error_from_exception(
    exc: Exception, wrapped: Mapping[str, _RecordingAdapter]
) -> ObservedError:
    from app.services.provider_resilience import ProviderCallFailure

    if isinstance(exc, ProviderCallFailure):
        info = exc.error_info
        return ObservedError(info.code, info.can_fallback, info.billable)
    # The gateway wrapped the provider failure; recover the raw error_info the
    # recording adapter stashed.
    for adapter in wrapped.values():
        if adapter.error_info is not None:
            info = adapter.error_info
            return ObservedError(info.code, info.can_fallback, info.billable)
    code = getattr(exc, "code", type(exc).__name__)
    return ObservedError(str(code), False, False)


def _final_operation(operations: Any) -> Mapping[str, Any] | None:
    by_id = getattr(operations, "_by_id", None)
    if not by_id:
        return None
    operation = next(iter(by_id.values()))
    return {
        "state": operation.state.value,
        "provider_operation_id": operation.provider_operation_id,
    }


# ---------------------------------------------------------------------------
# expect checks
# ---------------------------------------------------------------------------
def check_expectations(scenario: Scenario, observation: Observation) -> list[str]:
    """Assert the scenario's ``expect`` block against an observation.

    Returns a list of human-readable mismatch strings (empty = pass). The
    conformance suite fails the test when this is non-empty.
    """
    expect = scenario.data.get("expect") or {}
    problems: list[str] = []

    if "output" in expect and observation.output != expect["output"]:
        problems.append(
            f"output mismatch:\n  expected={expect['output']}\n  actual={observation.output}"
        )
    if "usage" in expect:
        for key, value in expect["usage"].items():
            actual = (observation.usage or {}).get(key)
            if actual != value:
                problems.append(f"usage.{key}: expected {value}, got {actual}")
    if "error" in expect:
        exp = expect["error"]
        act = observation.error.to_json() if observation.error is not None else None
        if act is None:
            problems.append(f"error expected {exp} but the call did not raise")
        else:
            for key, value in exp.items():
                if act.get(key) != value:
                    problems.append(f"error.{key}: expected {value}, got {act.get(key)}")
    if "requests" in expect:
        problems.extend(_check_request_shapes(expect["requests"], observation.requests))
    if "cleanup" in expect:
        problems.extend(_check_request_shapes(expect["cleanup"], observation.cleanup))
    if "operation" in expect:
        exp = expect["operation"]
        act = observation.operation or {}
        for key, value in exp.items():
            if act.get(key) != value:
                problems.append(f"operation.{key}: expected {value}, got {act.get(key)}")
    return problems


def _check_request_shapes(
    expected: Sequence[Mapping[str, Any]], actual: Sequence[OutgoingRequest]
) -> list[str]:
    """Each expected shape must match the actual request at the same position."""
    problems: list[str] = []
    if len(expected) != len(actual):
        problems.append(
            f"request count: expected {len(expected)}, got {len(actual)} "
            f"({[r.path for r in actual]})"
        )
        return problems
    for index, (want, got) in enumerate(zip(expected, actual, strict=False)):
        where = f"request[{index}]"
        if "method" in want and want["method"].upper() != got.method:
            problems.append(f"{where}.method: expected {want['method']}, got {got.method}")
        if "host" in want and want["host"] != got.host:
            problems.append(f"{where}.host: expected {want['host']}, got {got.host}")
        if "path" in want and want["path"] != got.path:
            problems.append(f"{where}.path: expected {want['path']}, got {got.path}")
        if "header_names" in want:
            missing = sorted(
                {n.lower() for n in want["header_names"]} - set(got.header_names)
            )
            if missing:
                problems.append(
                    f"{where}.header_names missing {missing} (got {got.header_names})"
                )
        if "body_keys" in want and tuple(sorted(want["body_keys"])) != got.body_keys:
            problems.append(
                f"{where}.body_keys: expected {sorted(want['body_keys'])}, "
                f"got {list(got.body_keys)}"
            )
        if "body" in want and want["body"] != (got.body or {}):
            problems.append(f"{where}.body: expected {want['body']}, got {got.body}")
    return problems


# ---------------------------------------------------------------------------
# Differential comparison
# ---------------------------------------------------------------------------
_RANDOM_KEY_HINTS = ("id", "uuid", "key", "token", "name", "ref", "url")


def _normalize_request(request: OutgoingRequest, *, ignore: frozenset[str]) -> dict[str, Any]:
    """Normalize one request for differential comparison.

    Ignores random identifiers: path segments that look like a uuid/hex/opaque
    token are replaced with ``*``, and body values whose key hints at an id/name/
    key/url are blanked. Header names, method, logical host, path *shape* and
    body *keys* survive, which is what parity compares.
    """
    path = _normalize_path(request.path)
    body = None
    if request.body is not None and "body" not in ignore:
        body = {k: ("*" if _is_random_key(k) else v) for k, v in request.body.items()}
    return {
        "method": request.method,
        "host": request.host if "host" not in ignore else "*",
        "path": path,
        "header_names": list(request.header_names),
        "body_keys": list(request.body_keys),
        "body": body,
        "is_upload": request.is_upload,
        "is_cleanup": request.is_cleanup,
    }


def _normalize_path(path: str) -> str:
    return "/".join("*" if _looks_random(segment) else segment for segment in path.split("/"))


def _looks_random(segment: str) -> bool:
    if not segment:
        return False
    stripped = segment.replace("-", "")
    if len(stripped) >= 16 and all(c in "0123456789abcdefABCDEF" for c in stripped):
        return True
    # A long opaque token (job/task id) with mixed alnum.
    return len(segment) >= 20 and any(c.isdigit() for c in segment) and segment.isalnum()


def _is_random_key(key: str) -> bool:
    low = key.lower()
    return any(hint in low for hint in _RANDOM_KEY_HINTS)


def compare_observations(
    a: Observation, b: Observation, *, ignore: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """Compare two observations for behavioral parity (differential suite).

    Compares the outgoing request *sequence*, the typed outputs, usage, the error
    class and the cleanup calls. IGNORED, by a documented normalization: route
    ids and revision strings (never part of an Observation), and random
    identifiers — uuids, media names, idempotency keys — in request paths
    (segments that look like a hex/opaque token become ``*``) and in request
    bodies (values under keys hinting ``id``/``name``/``key``/``url``/``ref``/
    ``token`` are blanked). Pass additional dimensions to ``ignore`` (any of
    ``"host"``, ``"body"``, ``"usage"``, ``"output"``, ``"requests"``,
    ``"cleanup"``) to drop them from the comparison.
    """
    ignore = frozenset(ignore)
    problems: list[str] = []

    if "requests" not in ignore:
        a_reqs = [_normalize_request(r, ignore=ignore) for r in a.requests]
        b_reqs = [_normalize_request(r, ignore=ignore) for r in b.requests]
        if a_reqs != b_reqs:
            problems.append(
                "request sequences differ:\n"
                f"  a={json.dumps(a_reqs, indent=2)}\n"
                f"  b={json.dumps(b_reqs, indent=2)}"
            )
    if "output" not in ignore and a.output != b.output:
        problems.append(f"outputs differ:\n  a={a.output}\n  b={b.output}")
    if "usage" not in ignore and a.usage != b.usage:
        problems.append(f"usage differs:\n  a={a.usage}\n  b={b.usage}")
    a_err = a.error.to_json() if a.error is not None else None
    b_err = b.error.to_json() if b.error is not None else None
    if a_err != b_err:
        problems.append(f"error classes differ:\n  a={a_err}\n  b={b_err}")
    if "cleanup" not in ignore:
        a_cleanup = [_normalize_request(r, ignore=ignore) for r in a.cleanup]
        b_cleanup = [_normalize_request(r, ignore=ignore) for r in b.cleanup]
        if a_cleanup != b_cleanup:
            problems.append(f"cleanup calls differ:\n  a={a_cleanup}\n  b={b_cleanup}")
    return problems


__all__ = [
    "CAPABILITY_SLUGS",
    "SENTINEL_SECRET",
    "Observation",
    "ObservedError",
    "OutgoingRequest",
    "Scenario",
    "ScenarioError",
    "build_request",
    "capabilities_with_scenarios",
    "check_expectations",
    "compare_observations",
    "discover_scenarios",
    "discover_smoke_scenarios",
    "load_scenario",
    "run_template_scenario",
    "write_sentinel_bundle",
]
