"""Manual live-provider run for the Task 17 template routes (Tier 1 gate).

Revision 5 deferred the live runs and deleted the archived adapters, so there is
no second side to compare against: this test now runs the **template route
alone** against the real service and validates it on its own terms.

Every test is marked ``@pytest.mark.live_provider`` and skipped unless
``MOTIONLY_LIVE_INFERENCE=1`` is set (the only case the Task 1 no-network guard
stands down). The run is manual, operator-approved and paid; it never runs in CI
(invariant 9). Each run is recorded in ``docs/ai-registry/live-runs.md``. To
compare against the archived adapters, check out the tag
``archive/pre-neutral-inference`` and run them there (see that document).

For each capability the test:

* builds the template route from the operator registry (real base URLs and auth
  secret refs) and sends one small synthetic sample through it;
* asserts the typed output is schema-valid for the capability;
* asserts the outgoing request sequence matches the route's **recorded live
  scenario** — the same methods, path shapes, header names and body keys;
* confirms cleanup at the provider where the route's mapping has a ``cleanup``
  stage;
* asserts no secret value appears in the captured logs;
* writes a redacted response shape (keys and types only) for fixture comparison.

Inputs (environment):

* ``MOTIONLY_LIVE_INFERENCE=1`` — authorizes the run.
* ``MOTIONLY_LIVE_REGISTRY`` — path to the operator registry containing the
  example routes (real base URLs, real auth secret refs).
* ``AI_SECRETS_BUNDLE_FILE`` — the generic secret bundle the registry references.
* ``MOTIONLY_LIVE_SAMPLES_DIR`` — operator-supplied samples (never committed):
  ``objects.jpg``, ``two-speakers.wav``, ``voice-over-music.wav``.
* ``MOTIONLY_LIVE_CAPABILITIES`` — optional comma list to run a subset
  (``objects,diarization,stems``).
* ``MOTIONLY_LIVE_RESULTS_DIR`` — optional; where to write the machine-readable
  result. Defaults to a pytest ``tmp_path``.

The recorded live scenario per capability is ``live.json`` under the capability's
scenario directory (``docs/ai-registry/examples/scenarios/<capability>/``). It
carries the operator registry/route ids and the expected ``requests`` / optional
``cleanup`` request shapes. It references the operator registry indirectly: the
live runtime loads ``MOTIONLY_LIVE_REGISTRY`` rather than the example document.

This file contains no vendor names.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from tests.support import template_scenarios as ts

# The operator sample file per capability (plan: image, ~30 s two-speaker speech,
# ~30 s voice over music).
_SAMPLE_BY_CAPABILITY = {
    "vision.object_detection": "objects.jpg",
    "audio.diarization": "two-speakers.wav",
    "audio.stem_separation": "voice-over-music.wav",
}

# The schema-valid typed-output keys per capability (plan Task 17 typed outputs).
_OUTPUT_SCHEMA = {
    "vision.object_detection": ("detections",),
    "audio.diarization": ("segments",),
    "audio.stem_separation": ("stems",),
}

_LIVE_ENABLED = os.getenv("MOTIONLY_LIVE_INFERENCE") == "1"


def _selected_capabilities() -> list[str]:
    raw = os.getenv("MOTIONLY_LIVE_CAPABILITIES", "").strip()
    if not raw:
        return list(ts.CAPABILITY_SLUGS)
    slug_to_cap = {slug: cap for cap, slug in ts.CAPABILITY_SLUGS.items()}
    selected: list[str] = []
    for token in (t.strip() for t in raw.split(",") if t.strip()):
        if token in ts.CAPABILITY_SLUGS:
            selected.append(token)
        elif token in slug_to_cap:
            selected.append(slug_to_cap[token])
    return selected or list(ts.CAPABILITY_SLUGS)


def _results_dir(tmp_path: Path) -> Path:
    configured = os.getenv("MOTIONLY_LIVE_RESULTS_DIR")
    directory = Path(configured) if configured else tmp_path / "live-results"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sample_path(capability: str) -> Path:
    samples_dir = os.getenv("MOTIONLY_LIVE_SAMPLES_DIR")
    if not samples_dir:
        pytest.skip("MOTIONLY_LIVE_SAMPLES_DIR is not set")
    path = Path(samples_dir) / _SAMPLE_BY_CAPABILITY[capability]
    if not path.is_file():
        pytest.skip(f"sample {path} is missing")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _live_scenario_for(capability: str) -> Path | None:
    """The operator-recorded live scenario for a capability, if present.

    Named ``live.json`` under the capability's scenario directory; it carries the
    operator route id and the expected ``requests`` / optional ``cleanup`` shapes.
    Absent until the operator records a run, so the test skips cleanly.
    """
    live = ts.SCENARIOS_DIR / capability / "live.json"
    return live if live.is_file() else None


def _redacted_shape(value: object) -> object:
    """Keys and types only — never values — for a fixture-comparable shape."""
    if isinstance(value, dict):
        return {str(k): _redacted_shape(v) for k, v in value.items()}
    if isinstance(value, list):
        inner = _redacted_shape(value[0]) if value else None
        return [inner, f"...len={len(value)}"]
    return type(value).__name__


def _write_result(results_dir: Path, capability: str, payload: dict) -> None:
    path = results_dir / f"live-{ts.CAPABILITY_SLUGS[capability]}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _assert_no_secret_in_logs(caplog, secrets: list[str]) -> None:
    for record in caplog.records:
        message = record.getMessage()
        for secret in secrets:
            assert secret and secret not in message, (
                f"a secret value leaked into a log record: {record.name}"
            )


def _resolved_secrets(bundle_path: str | None) -> list[str]:
    """Every secret value in the bundle, so the log scan can look for a leak."""
    if not bundle_path or not Path(bundle_path).is_file():
        return []
    try:
        bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(v) for v in bundle.values() if isinstance(v, str) and v]


def _schema_problems(capability: str, output: Mapping[str, object] | None) -> list[str]:
    """The typed output is schema-valid for the capability (keys present)."""
    if not isinstance(output, Mapping):
        return [f"{capability}: output is not a typed object: {type(output).__name__}"]
    missing = [key for key in _OUTPUT_SCHEMA[capability] if key not in output]
    return [f"{capability}: output missing {missing}"] if missing else []


class _RecordingLiveTransport(httpx.BaseTransport):
    """Wraps the real HTTP transport and records every outgoing request.

    Request *values* are never recorded (they may carry a secret); only the
    method, logical host, path, header *names* and top-level JSON body *keys*
    travel into the :class:`OutgoingRequest`, exactly as the fake harness records
    them, so the recorded live scenario compares the same dimensions.
    """

    def __init__(self) -> None:
        self._inner = httpx.HTTPTransport(trust_env=False)
        self.records: list[ts.OutgoingRequest] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        is_upload = "application/octet-stream" in request.headers.get("content-type", "")
        body_keys: tuple[str, ...] = ()
        if not is_upload and request.content:
            try:
                parsed = json.loads(request.content)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                body_keys = tuple(sorted(str(k) for k in parsed))
        self.records.append(
            ts.OutgoingRequest(
                method=request.method.upper(),
                host=request.url.host,
                path=request.url.path,
                header_names=tuple(sorted(k.lower() for k in request.headers)),
                body_keys=body_keys,
                is_upload=is_upload,
            )
        )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


@pytest.mark.live_provider
@pytest.mark.parametrize("capability", list(ts.CAPABILITY_SLUGS))
def test_live_template_route(capability: str, tmp_path: Path, caplog) -> None:
    if not _LIVE_ENABLED:
        pytest.skip("MOTIONLY_LIVE_INFERENCE is not set")
    if capability not in _selected_capabilities():
        pytest.skip(f"{capability}: not in MOTIONLY_LIVE_CAPABILITIES")
    registry = os.getenv("MOTIONLY_LIVE_REGISTRY")
    if not registry:
        pytest.skip("MOTIONLY_LIVE_REGISTRY is not set")
    secret_bundle = os.getenv("AI_SECRETS_BUNDLE_FILE")
    if not secret_bundle:
        pytest.skip("AI_SECRETS_BUNDLE_FILE is not set")

    sample = _sample_path(capability)
    scenario_path = _live_scenario_for(capability)
    if scenario_path is None:
        pytest.skip(f"{capability}: no recorded live scenario (live.json) available")

    from app.core.config import get_settings
    from app.services.inference_operations import InMemoryInferenceOperationRepository
    from app.services.inference_runtime import build_inference_runtime
    from app.services.inference_secrets import SecretResolver
    from app.services.model_gateway import ModelCallContext, ModelGatewayError
    from app.services.provider_resilience import ProviderCallFailure

    scenario = ts.load_scenario(scenario_path)
    settings = get_settings().model_copy(
        update={
            "ai_registry_file": registry,
            "ai_secrets_bundle_file": secret_bundle,
            "redis_url": None,
        }
    )
    transport = _RecordingLiveTransport()
    runtime = build_inference_runtime(
        settings,
        operations_repository=InMemoryInferenceOperationRepository(),
        secret_resolver=SecretResolver(settings=settings),
        transport=transport,
    )
    request = ts.build_request(scenario, tmp_path)
    context = ModelCallContext(idempotency_key=f"live-{scenario.route_id}")

    results_dir = _results_dir(tmp_path)
    output_json: object | None = None
    error: str | None = None
    with caplog.at_level(logging.DEBUG):
        try:
            result = runtime.gateway.invoke(request, context=context)
            output_json = ts._output_to_json(result.output)  # noqa: SLF001
        except (ProviderCallFailure, ModelGatewayError) as exc:
            error = getattr(exc, "code", type(exc).__name__)

    cleanup_paths = ts._cleanup_paths(scenario)  # noqa: SLF001
    requests = tuple(
        ts.OutgoingRequest(
            method=r.method,
            host=r.host,
            path=r.path,
            header_names=r.header_names,
            body_keys=r.body_keys,
            is_upload=r.is_upload,
            is_cleanup=any(
                r.path == p or r.path.startswith(p + "/") for p in cleanup_paths
            ),
        )
        for r in transport.records
    )
    observation = ts.Observation(requests=requests, output=output_json)

    schema_problems = _schema_problems(capability, output_json)
    expect = scenario.data.get("expect") or {}
    request_problems = ts._check_request_shapes(  # noqa: SLF001
        expect.get("requests", []), observation.requests
    )
    cleanup_problems: list[str] = []
    if "cleanup" in expect:
        cleanup_problems = ts._check_request_shapes(  # noqa: SLF001
            expect["cleanup"], observation.cleanup
        )

    _assert_no_secret_in_logs(caplog, _resolved_secrets(secret_bundle))

    problems = schema_problems + request_problems + cleanup_problems
    outcome = "pass" if error is None and not problems else "fail"
    _write_result(
        results_dir,
        capability,
        {
            "date": _dt.datetime.now(_dt.UTC).isoformat(),
            "capability": capability,
            "route_id": scenario.route_id,
            "sample_sha256": _sha256(sample),
            "outcome": outcome,
            "error": error,
            "schema_problems": schema_problems,
            "request_problems": request_problems,
            "cleanup_problems": cleanup_problems,
            "response_shape": _redacted_shape(output_json),
        },
    )

    assert error is None, f"live call failed with {error}"
    assert not schema_problems, "typed output is not schema-valid:\n" + "\n".join(schema_problems)
    assert not request_problems, "request sequence mismatch:\n" + "\n".join(request_problems)
    assert not cleanup_problems, "cleanup mismatch:\n" + "\n".join(cleanup_problems)
