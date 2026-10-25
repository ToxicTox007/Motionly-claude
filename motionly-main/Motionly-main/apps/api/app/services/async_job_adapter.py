"""The ``async_job`` model-gateway adapter — Tier 1 slice, blocking mode (Task 16B).

One :class:`AsyncJobAdapter` instance stands for one registry adapter record
(a base URL, auth and shared headers). Each route served by that adapter supplies
an :class:`AsyncJobRouteConfig` — the Section 2.10 ``async_job`` ``mapping`` plus
the route options the slice reads (``input_transform.audio``, ``upload_filename``)
and the blocking poll/timeout knobs.

It runs the Section 2.10 ``async_job`` slice in **blocking mode only**:

* a ``vars`` block evaluated once per call, then ordered ``steps`` with
  ``capture``/``require``/``auth``;
* a ``commit`` step that marks the billable boundary — after it, no fallback;
* a reserved :class:`~app.services.inference_operations.InferenceOperation` that
  makes the submit route-affine (plan invariant 6): a worker retry after commit
  resumes polling the recorded job rather than resubmitting;
* a bounded poll loop with a status map, transient-error tolerance, backoff and
  a deadline;
* a ``result`` mapping (diarization) or ``artifacts`` download (stems), both read
  from the final successful poll response;
* best-effort ``cleanup`` after both success and failure;
* ``input_transform.audio`` (ffmpeg via ``media_tool_env``) and an adapter-managed
  temp directory removed on every exit path.

Auth is applied by the adapter, never by a template (Section 2.10 "Not
allowed"). ``auth: none`` steps and artifact downloads go to a captured URL that
must pass the SSRF policy in ``media_url_policy``; adapter credentials and extra
headers are never sent on those requests, and the shared client carries no auth
headers by default (they are set per adapter-auth request).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx

from app.services.inference_http import (
    AuthApplier,
    base_headers,
    build_client,
)
from app.services.inference_normalization import (
    map_stem_name,
    sort_diarization_segments,
)
from app.services.inference_operations import (
    InferenceOperation,
    InferenceOperationRepository,
    OperationState,
    route_snapshot,
)
from app.services.inference_templates import (
    TemplateContext,
    TemplateError,
    TemplateMissing,
    evaluate_value,
    render_path,
    resolve_ref,
    resolve_relative_ref,
    validate_mapping,
)
from app.services.media_url_policy import (
    MediaUrlPolicyError,
    safe_http_stream,
    validate_outbound_media_url,
)
from app.services.model_gateway import (
    _ACTIVE_CALL_CONTEXT,
    AdapterResult,
    DiarizationOutput,
    DiarizationRequest,
    DiarizationSegmentOutput,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    StemSeparationOutput,
    StemSeparationRequest,
    diarization_billable_seconds,
    inherited_call_context,
    operation_for_request,
    stem_duration_units,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
    may_have_been_sent,
)
from app.services.subprocess_env import media_tool_env

logger = logging.getLogger(__name__)

ADAPTER_TYPE = "async_job"

# A neutral temp-directory prefix (no vendor name). Task 17 adds this prefix to
# the scale.py cleanup allowlist so a caller-handed stem directory is removed.
ASYNC_JOB_TEMP_PREFIX = "motionly-async-stems-"


@dataclass(frozen=True)
class AsyncPollOutcome:
    """The result of one non-blocking poll (Tier 3, Task 28).

    ``pending`` with ``retry_after_s`` means the job is still running and the
    caller should re-enqueue a delayed poll. ``pending=False`` carries the
    terminal :class:`AdapterResult`.
    """

    pending: bool
    retry_after_s: float = 0.0
    result: AdapterResult | None = None

# States that mean a reserved operation is already at or past the commit point:
# its submit must not be repeated, and a worker resumes polling instead.
_PAST_COMMIT_STATES = frozenset(
    {
        OperationState.SUBMITTED,
        OperationState.SUBMIT_UNKNOWN,
        OperationState.RUNNING,
        OperationState.NEEDS_REVIEW,
    }
)

# The audio runner signature: command -> subprocess.CompletedProcess. The default
# runs ffmpeg with the restricted media_tool_env(); tests inject a fake runner.
AudioRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_audio_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env=media_tool_env(),
    )


@dataclass(frozen=True)
class AudioTransform:
    """``input_transform.audio``: an audio-only rendition before upload."""

    codec: str
    sample_rate: int
    channels: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> AudioTransform:
        return cls(
            codec=str(raw["codec"]),
            sample_rate=int(raw["sample_rate"]),  # type: ignore[arg-type]
            channels=int(raw["channels"]),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class AsyncJobRouteConfig:
    """Per-route configuration for one ``async_job`` route.

    ``mapping`` is the Section 2.10 ``async_job`` mapping, validated at
    construction (before activation). ``params`` are route parameters the
    template may read via ``params.*``. ``input_transform_audio`` and
    ``upload_filename`` are the two Section 2.10 route options the slice reads.
    """

    mapping: Mapping[str, object]
    params: Mapping[str, object] = field(default_factory=dict)
    input_transform_audio: AudioTransform | None = None
    upload_filename: str | None = None
    # Blocking poll/timeout knobs; the mapping's poll block overrides these.
    request_timeout_s: float = 60.0
    poll_interval_s: float = 5.0
    poll_backoff_factor: float = 1.5
    poll_max_interval_s: float = 30.0
    poll_max_wait_s: float = 900.0
    max_artifact_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.request_timeout_s <= 0 or self.poll_interval_s <= 0:
            raise ValueError("async_job route timeouts must be positive")
        errors = validate_mapping(ADAPTER_TYPE, self.mapping)
        if errors:
            raise ValueError("async_job route mapping is invalid: " + "; ".join(errors))


class AsyncJobAdapter:
    """A declarative async-job adapter driven entirely by registry config."""

    def __init__(
        self,
        *,
        base_url: str,
        auth: AuthApplier,
        operations_repository: InferenceOperationRepository,
        provider: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        routes: Mapping[str, AsyncJobRouteConfig] | None = None,
        audio_runner: AudioRunner = _default_audio_runner,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        dns_resolver: object | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("async_job adapter base_url is required")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("async_job adapter base_url must be an absolute HTTPS URL")
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._operations = operations_repository
        self._provider = provider
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport
        self._routes: dict[str, AsyncJobRouteConfig] = dict(routes or {})
        self._audio_runner = audio_runner
        self._sleep = sleep
        self._clock = clock
        self._dns_resolver = dns_resolver

    def register_route(self, route_id: str, config: AsyncJobRouteConfig) -> None:
        if not route_id.strip():
            raise ValueError("route_id cannot be empty")
        if route_id in self._routes:
            raise ValueError(f"async_job route already configured: {route_id}")
        self._routes[route_id] = config

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        config = self._routes.get(route.route_id)
        if config is None:
            raise ValueError(f"async_job adapter has no mapping for route {route.route_id!r}")
        if isinstance(request, (DiarizationRequest, StemSeparationRequest)):
            return self._run(route=route, request=request, config=config)
        raise TypeError(
            "async_job Tier 1 adapter supports DiarizationRequest and "
            f"StemSeparationRequest; got {type(request).__name__}"
        )

    # -- orchestration ------------------------------------------------------
    def _run(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
    ) -> AdapterResult:
        provider = self._provider or route.provider
        temp_dir = Path(tempfile.mkdtemp(prefix=ASYNC_JOB_TEMP_PREFIX))
        try:
            return self._run_in_dir(
                route=route,
                request=request,
                config=config,
                provider=provider,
                temp_dir=temp_dir,
            )
        finally:
            # Transformed inputs and intermediate files are removed on every exit
            # path. On a stems success the caller gets its own StemSeparationOutput
            # directory (a different temp dir), so removing this one is always safe.
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _run_in_dir(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        provider: str,
        temp_dir: Path,
    ) -> AdapterResult:
        # Prepare the raw upload bytes (with the optional audio transform).
        upload_path = self._prepare_input(request, config, temp_dir)

        # Reserve the operation (route-affine). The idempotency key comes from the
        # active ModelCallContext (``inherited_call_context`` propagates only the
        # tenant, so the key is read directly). A missing key gets a fresh one so
        # a single call is correct, but *resume* needs a caller-supplied key —
        # Task 17 wires callers that pass one.
        context = inherited_call_context()
        active = _ACTIVE_CALL_CONTEXT.get()
        workspace_id = _as_uuid(context.workspace_id)
        project_id = _as_uuid(context.project_id) if context.project_id is not None else None
        caller_key = active.idempotency_key if active is not None else None
        idempotency_key = caller_key or f"async-{route.route_id}-{_fresh_key()}"
        snapshot = route_snapshot(
            adapter_id=route.adapter,
            adapter_type=ADAPTER_TYPE,
            base_url=self._base_url,
            model=route.model,
            model_revision=route.model_revision,
            mapping_version=route.capability_id or route.capability.value,
            secret_ref_name=None,
        )
        operation, _created = self._operations.reserve(
            workspace_id=workspace_id,
            project_id=project_id,
            capability=operation_for_request(request).value,
            idempotency_key=idempotency_key,
            route_id=route.route_id,
            model=route.model,
            model_revision=route.model_revision,
            route_snapshot=snapshot,
        )

        mapping = config.mapping
        vars_state: dict[str, object] = {}

        if operation.state in _PAST_COMMIT_STATES and operation.provider_operation_id:
            # A worker retry after commit: resume polling with stored vars. Never
            # resubmit (plan invariant 6, Task 17 stems "one improvement").
            vars_state = dict(operation.resume_vars)
        elif operation.state == OperationState.SUCCEEDED:
            # A terminal success: re-poll once to fetch the result rather than
            # re-running steps. Documented: a succeeded operation is not re-run.
            vars_state = dict(operation.resume_vars)
        elif operation.state in {OperationState.FAILED, OperationState.CANCELLED}:
            raise ProviderCallFailure(
                provider=provider,
                error_info=ProviderErrorInfo(
                    "provider_request_rejected",
                    False,
                    fallback_eligible=False,
                    circuit_worthy=False,
                    billable=False,
                ),
                message="This async operation already ended; it will not be re-run.",
            )

        # Everything from the first step onward runs under one cleanup guard:
        # cleanup is best-effort after both success and failure, and is skipped
        # for any request whose vars were never captured (Section 2.10).
        with self._client() as client:
            try:
                if operation.state == OperationState.RESERVED:
                    self._run_steps(
                        route=route,
                        request=request,
                        config=config,
                        provider=provider,
                        operation=operation,
                        upload_path=upload_path,
                        vars_state=vars_state,
                    )
                final_response = self._poll(
                    client=client,
                    mapping=mapping,
                    config=config,
                    provider=provider,
                    vars_state=vars_state,
                    params=config.params,
                    operation=operation,
                )
                result = self._build_result(
                    route=route,
                    request=request,
                    config=config,
                    provider=provider,
                    final_response=final_response,
                    vars_state=vars_state,
                )
            finally:
                self._cleanup(client, mapping, config, vars_state, provider)
        self._operations.transition(
            operation.id,
            to_state=OperationState.SUCCEEDED,
            expected_from=None,
            actual_cost_usd=None,
        )
        return result

    # -- steps --------------------------------------------------------------
    def _run_steps(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        provider: str,
        operation: InferenceOperation,
        upload_path: Path,
        vars_state: dict[str, object],
    ) -> None:
        mapping = config.mapping
        steps = list(mapping["steps"])  # type: ignore[arg-type]
        vars_state.update(self._initial_vars(mapping, config, provider))
        committed = False

        with self._client() as client:
            for step in steps:
                assert isinstance(step, Mapping)
                is_commit = step.get("commit") is True
                if is_commit and not committed:
                    self._operations.transition(
                        operation.id,
                        to_state=OperationState.SUBMITTING,
                        expected_from={OperationState.RESERVED},
                    )
                try:
                    self._run_one_step(
                        client=client,
                        step=step,
                        config=config,
                        provider=provider,
                        vars_state=vars_state,
                        upload_path=upload_path,
                    )
                except ProviderCallFailure as exc:
                    self._on_step_failure(
                        operation=operation,
                        is_commit=is_commit,
                        error_info=exc.error_info,
                        provider=provider,
                    )
                    raise
                if is_commit:
                    committed = True
                    provider_operation_id = self._commit_operation_id(step, vars_state)
                    # Persist resume vars (non-secret captures) before marking
                    # submitted so a crash right after cannot lose them.
                    self._operations.save_resume_vars(
                        operation.id, _resume_vars(vars_state)
                    )
                    self._operations.transition(
                        operation.id,
                        to_state=OperationState.SUBMITTED,
                        expected_from={OperationState.SUBMITTING},
                        provider_operation_id=provider_operation_id,
                    )

    def _on_step_failure(
        self,
        *,
        operation: InferenceOperation,
        is_commit: bool,
        error_info: ProviderErrorInfo,
        provider: str,
    ) -> None:
        if is_commit and may_have_been_sent(error_info):
            # The commit request may have reached the provider. Mark the operation
            # submit_unknown and raise a non-fallback failure (plan Section 2.5).
            self._operations.transition(
                operation.id,
                to_state=OperationState.SUBMIT_UNKNOWN,
                expected_from={OperationState.SUBMITTING},
                error_code=error_info.code,
            )
            raise ProviderCallFailure(
                provider=provider,
                error_info=ProviderErrorInfo(
                    error_info.code,
                    error_info.retryable,
                    error_info.status_code,
                    fallback_eligible=False,
                    circuit_worthy=error_info.should_open_circuit,
                    billable=error_info.billable,
                    sent=True,
                ),
                message="The async submit may have been sent; it will not fall back.",
            )
        # Any failure before the commit send (or on a non-commit step) is a plain
        # failure that may fall back. Mark the operation failed only if it had
        # already advanced to submitting.
        try:
            self._operations.transition(
                operation.id,
                to_state=OperationState.FAILED,
                expected_from={OperationState.SUBMITTING, OperationState.RESERVED},
                error_code=error_info.code,
            )
        except Exception:  # noqa: BLE001 - the transition is best-effort bookkeeping
            logger.warning("async_job_operation_fail_mark_skipped")

    def _run_one_step(
        self,
        *,
        client: httpx.Client,
        step: Mapping[str, object],
        config: AsyncJobRouteConfig,
        provider: str,
        vars_state: dict[str, object],
        upload_path: Path,
    ) -> None:
        ctx = self._ctx(vars_state, config.params)
        method = str(step["method"]).upper()
        auth_mode = str(step.get("auth", "adapter"))
        body_spec = step.get("body")

        if auth_mode == "none":
            url = self._resolve_captured_url(
                str(step["url"]), vars_state, require_https=self._requires_https(step)
            )
            self._check_requirements(step, vars_state, provider)
            self._request_no_auth(
                client=client,
                method=method,
                url=url,
                body_spec=body_spec,
                config=config,
                upload_path=upload_path,
                provider=provider,
                ctx=ctx,
            )
            return

        path = self._render_path(str(step["path"]), ctx, provider)
        payload = self._request_adapter(
            client=client,
            method=method,
            path=path,
            body_spec=body_spec,
            config=config,
            upload_path=upload_path,
            provider=provider,
            ctx=ctx,
        )
        self._capture(step, payload, vars_state, provider)
        # ``require`` validates the values this step just captured (for example a
        # freshly captured upload URL must be HTTPS before the auth:none upload).
        self._check_requirements(step, vars_state, provider)

    # -- poll ---------------------------------------------------------------
    def _poll(
        self,
        *,
        client: httpx.Client,
        mapping: Mapping[str, object],
        config: AsyncJobRouteConfig,
        provider: str,
        vars_state: dict[str, object],
        params: Mapping[str, object],
        operation: InferenceOperation,
    ) -> object:
        poll = mapping["poll"]
        assert isinstance(poll, Mapping)
        method = str(poll["method"]).upper()
        status_path = str(poll["status"])
        status_map = poll["status_map"]
        assert isinstance(status_map, Mapping)
        tolerate_retryable = poll.get("tolerate_retryable") is True
        interval = float(poll.get("interval_s", config.poll_interval_s))
        backoff = poll.get("backoff") or {}
        factor = float(backoff.get("factor", config.poll_backoff_factor))  # type: ignore[union-attr]
        max_interval = float(
            backoff.get("max_interval_s", config.poll_max_interval_s)  # type: ignore[union-attr]
        )
        max_wait = float(poll.get("max_wait_s", config.poll_max_wait_s))
        body_spec = poll.get("body")
        running_marked = False

        deadline = self._clock() + max_wait
        while True:
            ctx = self._ctx(vars_state, params)
            path = self._render_path(str(poll["path"]), ctx, provider)
            try:
                payload = self._request_adapter(
                    client=client,
                    method=method,
                    path=path,
                    body_spec=body_spec,
                    config=config,
                    upload_path=None,
                    provider=provider,
                    ctx=ctx,
                )
            except ProviderCallFailure as exc:
                if tolerate_retryable and exc.error_info.retryable:
                    payload = None  # Transient: keep polling until the deadline.
                else:
                    raise
            if payload is not None:
                state, billable, fallback = self._status_state(
                    payload, status_path, status_map, provider, vars_state
                )
                if state == "succeeded":
                    return payload
                if state in {"failed", "retryable_failed"}:
                    raise self._status_failure(state, billable, fallback, provider)
                if state == "running" and not running_marked:
                    running_marked = self._mark_running(operation)
            if self._clock() + interval > deadline:
                # Deadline after commit: provider_timeout, retryable, no fallback.
                raise ProviderCallFailure(
                    provider=provider,
                    error_info=ProviderErrorInfo(
                        "provider_timeout", True, fallback_eligible=False, sent=True
                    ),
                    message="The async job did not finish before the deadline.",
                )
            self._sleep(interval)
            interval = min(max_interval, interval * factor)

    # -- non-blocking mode (Tier 3, Task 28) --------------------------------
    @staticmethod
    def is_non_blocking(config: AsyncJobRouteConfig) -> bool:
        """Whether this route's mapping requests ``mode: non_blocking``."""
        return str(config.mapping.get("mode", "blocking")) == "non_blocking"

    def submit_async(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        operation: InferenceOperation,
    ) -> InferenceOperation:
        """Run the steps up to and including commit, then return (no poll).

        The delayed-poll engine (``inference_polling``) owns the operation
        lifecycle; this method only advances a freshly-``reserved`` operation
        through its steps and persists the resume vars. A non-``reserved``
        operation is returned untouched: a worker restart never resubmits
        (plan invariant 6). The upload temp dir is removed before returning.
        """
        if operation.state != OperationState.RESERVED:
            return self._operations.get(operation.id)
        provider = self._provider or route.provider
        temp_dir = Path(tempfile.mkdtemp(prefix=ASYNC_JOB_TEMP_PREFIX))
        try:
            upload_path = self._prepare_input(request, config, temp_dir)
            vars_state: dict[str, object] = {}
            self._run_steps(
                route=route,
                request=request,
                config=config,
                provider=provider,
                operation=operation,
                upload_path=upload_path,
                vars_state=vars_state,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return self._operations.get(operation.id)

    def poll_once_async(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        operation: InferenceOperation,
    ) -> AsyncPollOutcome:
        """Advance one poll on a submitted operation (no sleep, no loop).

        Returns an :class:`AsyncPollOutcome`: ``pending`` with ``retry_after_s``
        when the job is still running, or ``result`` when it has succeeded. A
        provider failure state raises :class:`ProviderCallFailure`. Resume state
        comes from the persisted operation, so this is safe after a restart and
        never resubmits.
        """
        provider = self._provider or route.provider
        mapping = config.mapping
        poll = mapping["poll"]
        assert isinstance(poll, Mapping)
        vars_state = dict(operation.resume_vars)
        method = str(poll["method"]).upper()
        status_path = str(poll["status"])
        status_map = poll["status_map"]
        assert isinstance(status_map, Mapping)
        tolerate_retryable = poll.get("tolerate_retryable") is True
        interval = float(poll.get("interval_s", config.poll_interval_s))
        body_spec = poll.get("body")

        with self._client() as client:
            ctx = self._ctx(vars_state, config.params)
            path = self._render_path(str(poll["path"]), ctx, provider)
            try:
                payload = self._request_adapter(
                    client=client,
                    method=method,
                    path=path,
                    body_spec=body_spec,
                    config=config,
                    upload_path=None,
                    provider=provider,
                    ctx=ctx,
                )
            except ProviderCallFailure as exc:
                if tolerate_retryable and exc.error_info.retryable:
                    return AsyncPollOutcome(pending=True, retry_after_s=interval)
                self._fail_operation(operation, exc.error_info)
                raise
            state, billable, fallback = self._status_state(
                payload, status_path, status_map, provider, vars_state
            )
            if state == "running":
                self._mark_running(operation)
                return AsyncPollOutcome(pending=True, retry_after_s=interval)
            if state in {"failed", "retryable_failed"}:
                failure = self._status_failure(state, billable, fallback, provider)
                self._fail_operation(operation, failure.error_info)
                raise failure
            # Succeeded: build the result and run best-effort cleanup once.
            try:
                result = self._build_result(
                    route=route,
                    request=request,
                    config=config,
                    provider=provider,
                    final_response=payload,
                    vars_state=vars_state,
                )
            finally:
                self._cleanup(client, mapping, config, vars_state, provider)
        self._operations.transition(
            operation.id,
            to_state=OperationState.SUCCEEDED,
            expected_from=None,
            actual_cost_usd=None,
        )
        return AsyncPollOutcome(pending=False, result=result)

    def _fail_operation(
        self, operation: InferenceOperation, error_info: ProviderErrorInfo
    ) -> None:
        try:
            self._operations.transition(
                operation.id,
                to_state=OperationState.FAILED,
                expected_from=None,
                error_code=error_info.code,
            )
        except Exception:  # noqa: BLE001 - best-effort terminal bookkeeping
            logger.warning("async_job_operation_fail_mark_skipped")

    def _status_state(
        self,
        payload: object,
        status_path: str,
        status_map: Mapping[str, object],
        provider: str,
        vars_state: Mapping[str, object] | None = None,
    ) -> tuple[str, bool, bool]:
        # ``vars`` is threaded so a status path keyed by a captured id (for
        # example ``response.result.{vars.task_id}.status``) resolves.
        ctx = TemplateContext(response=payload, vars=dict(vars_state or {}))
        try:
            raw = resolve_ref(status_path, ctx)
        except TemplateMissing as exc:
            raise self._malformed(provider) from exc
        entry = status_map.get(str(raw))
        if not isinstance(entry, Mapping):
            # A status the map does not list is a malformed response.
            raise self._malformed(provider)
        state = str(entry["state"])
        billable = entry.get("billable", True) is not False
        fallback = entry.get("fallback", False) is True
        return state, billable, fallback

    def _status_failure(
        self, state: str, billable: bool, fallback: bool, provider: str
    ) -> ProviderCallFailure:
        retryable = state == "retryable_failed"
        return ProviderCallFailure(
            provider=provider,
            error_info=ProviderErrorInfo(
                "provider_unavailable" if retryable else "provider_job_failed",
                retryable,
                fallback_eligible=fallback,
                billable=billable,
                sent=True,
            ),
            message=f"The async job ended with a {state} status.",
        )

    def _mark_running(self, operation: InferenceOperation) -> bool:
        try:
            self._operations.transition(
                operation.id,
                to_state=OperationState.RUNNING,
                expected_from={
                    OperationState.SUBMITTED,
                    OperationState.SUBMIT_UNKNOWN,
                },
            )
            return True
        except Exception:  # noqa: BLE001 - running mark is best-effort bookkeeping
            return False

    # -- result / artifacts -------------------------------------------------
    def _build_result(
        self,
        *,
        route: ModelRoute,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        provider: str,
        final_response: object,
        vars_state: dict[str, object],
    ) -> AdapterResult:
        mapping = config.mapping
        if isinstance(request, DiarizationRequest):
            segments = self._parse_diarization(mapping, final_response, provider)
            return AdapterResult(
                output=DiarizationOutput(
                    segments=segments,
                    raw_response={"segments": len(segments)},
                ),
                usage=ModelUsage(input_units=self._diarization_units(route, request)),
                usage_reported=True,
            )
        # Stems are written into a dedicated output directory that is removed on
        # failure and handed to the caller on success (Section 2.10 "handed to
        # the caller on success, as the archived stems adapter does today").
        output_dir = Path(tempfile.mkdtemp(prefix=ASYNC_JOB_TEMP_PREFIX))
        try:
            stems = self._download_artifacts(
                mapping, config, final_response, provider, output_dir, vars_state
            )
        except BaseException:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise
        return AdapterResult(
            output=StemSeparationOutput(stems=stems),
            usage=ModelUsage(input_units=self._stem_units(route, request)),
            usage_reported=True,
        )

    def _parse_diarization(
        self, mapping: Mapping[str, object], response: object, provider: str
    ) -> tuple[DiarizationSegmentOutput, ...]:
        result = mapping["result"]
        assert isinstance(result, Mapping)
        items = self._resolve_items(str(result["items"]), response, provider)
        fields = result["fields"]
        assert isinstance(fields, Mapping)
        segments: list[DiarizationSegmentOutput] = []
        for item in items:
            ctx = TemplateContext(response=None).with_item(item)
            try:
                speaker = self._field_value(fields["speaker"], ctx)
                start = self._field_value(fields["start_ms"], ctx)
                end = self._field_value(fields["end_ms"], ctx)
            except (TemplateMissing, TemplateError) as exc:
                raise self._malformed(provider) from exc
            try:
                segments.append(
                    DiarizationSegmentOutput(
                        speaker=str(speaker),
                        start_ms=int(start),  # type: ignore[arg-type]
                        end_ms=int(end),  # type: ignore[arg-type]
                    )
                )
            except (TypeError, ValueError) as exc:
                raise self._malformed(provider) from exc
        return sort_diarization_segments(segments)

    def _download_artifacts(
        self,
        mapping: Mapping[str, object],
        config: AsyncJobRouteConfig,
        response: object,
        provider: str,
        temp_dir: Path,
        vars_state: Mapping[str, object] | None = None,
    ) -> dict[str, Path]:
        artifacts = mapping["artifacts"]
        assert isinstance(artifacts, Mapping)
        items = self._resolve_items(str(artifacts["items"]), response, provider, vars_state)
        if not items:
            raise self._malformed(provider)  # An empty artifact list is malformed.
        name_map = artifacts["name_map"]
        assert isinstance(name_map, Mapping)
        name_map = {str(k): str(v) for k, v in name_map.items()}
        required = [str(name) for name in (artifacts.get("required") or [])]
        allowed_hosts = [str(host).lower() for host in artifacts["allowed_hosts"]]  # type: ignore[union-attr]
        force_https = artifacts.get("force_https") is True
        max_bytes = int(artifacts.get("max_bytes", config.max_artifact_bytes))  # type: ignore[arg-type]

        urls: dict[str, str] = {}
        for item in items:
            ctx = TemplateContext(response=None).with_item(item)
            try:
                raw_name = resolve_relative_ref(str(artifacts["name"]), ctx)
                raw_url = resolve_relative_ref(str(artifacts["url"]), ctx)
            except TemplateMissing as exc:
                raise self._malformed(provider) from exc
            stem_name = map_stem_name(raw_name, name_map)
            if stem_name is None:
                continue  # An artifact not in name_map is skipped.
            if not isinstance(raw_url, str) or not raw_url:
                raise self._malformed(provider)
            if stem_name in urls:
                raise self._malformed(provider)  # A duplicate mapped name is malformed.
            urls[stem_name] = self._artifact_url(raw_url, allowed_hosts, force_https, provider)

        for name in required:
            if name not in urls:
                raise self._malformed(provider)  # A missing required name is malformed.

        stems: dict[str, Path] = {}
        for stem_name, url in urls.items():
            target = temp_dir / f"{stem_name}.bin"
            self._download(url, target, max_bytes, provider)
            stems[stem_name] = target
        return stems

    def _artifact_url(
        self, raw_url: str, allowed_hosts: Sequence[str], force_https: bool, provider: str
    ) -> str:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        if force_https and scheme == "http":
            parsed = parsed._replace(scheme="https")
            scheme = "https"
            raw_url = urlunsplit(parsed)
        host = (parsed.hostname or "").rstrip(".").lower()
        if not self._host_allowed(host, allowed_hosts):
            raise self._malformed(provider)
        try:
            validate_outbound_media_url(raw_url, resolver=self._resolver())
        except MediaUrlPolicyError as exc:
            raise self._malformed(provider) from exc
        return raw_url

    @staticmethod
    def _host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
        for allowed in allowed_hosts:
            if allowed.startswith("*."):
                if host.endswith(allowed[1:]) and host != allowed[2:]:
                    return True
                if host == allowed[2:]:
                    return True
            elif host == allowed:
                return True
        return False

    def _download(self, url: str, target: Path, max_bytes: int, provider: str) -> None:
        written = 0
        try:
            # Pinned to the addresses validated for this URL (no DNS re-resolution
            # between the SSRF check and the connection), redirects re-validated.
            with self._pinned_stream(url, method="GET", headers={"accept": "*/*"}) as response:
                if response.status_code >= 400:
                    raise ProviderCallFailure(
                        provider=provider,
                        error_info=classify_provider_status(response.status_code),
                    )
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise self._malformed(provider)
                        output.write(chunk)
        except ProviderCallFailure:
            raise
        except MediaUrlPolicyError as exc:
            raise self._malformed(provider) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider=provider, error_info=classify_provider_exception(exc)
            ) from None
        if written == 0:
            raise self._malformed(provider)  # An empty artifact is malformed.

    # -- cleanup ------------------------------------------------------------
    def _cleanup(
        self,
        client: httpx.Client,
        mapping: Mapping[str, object],
        config: AsyncJobRouteConfig,
        vars_state: Mapping[str, object],
        provider: str,
    ) -> None:
        requests = mapping.get("cleanup")
        if not isinstance(requests, list):
            return
        for request in requests:
            if not isinstance(request, Mapping):
                continue
            if not self._cleanup_vars_available(request, vars_state):
                continue  # Skip when a referenced var was never captured.
            ctx = self._ctx(dict(vars_state), config.params)
            try:
                path = self._render_path(str(request["path"]), ctx, provider)
                self._request_adapter(
                    client=client,
                    method=str(request["method"]).upper(),
                    path=path,
                    body_spec=request.get("body"),
                    config=config,
                    upload_path=None,
                    provider=provider,
                    ctx=ctx,
                )
            except Exception as exc:  # noqa: BLE001 - cleanup never changes the result
                logger.warning(
                    "async_job_cleanup_failed",
                    extra={"error_type": type(exc).__name__},
                )

    @staticmethod
    def _cleanup_vars_available(
        request: Mapping[str, object], vars_state: Mapping[str, object]
    ) -> bool:
        names = _referenced_vars(request.get("body"))
        names |= _path_vars(str(request.get("path", "")))
        return all(name in vars_state for name in names)

    # -- HTTP helpers -------------------------------------------------------
    def _client(self) -> httpx.Client:
        # The shared client carries NO auth headers by default (Section 2.10):
        # adapter credentials are set per adapter-auth request, never globally.
        return build_client(timeout_s=self._base_timeout(), transport=self._transport)

    @contextmanager
    def _pinned_stream(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        content: object = None,
    ) -> Iterator[httpx.Response]:
        """A request to a captured URL, pinned to its validated public addresses.

        Uses ``safe_http_stream`` (the codebase's DNS-pinned fetcher), so a host
        cannot rebind to a private or metadata address between validation and
        connect. The injected transport is honoured for tests.
        """

        @contextmanager
        def factory(
            request_method: str, request_url: str, **kwargs: object
        ) -> Iterator[httpx.Response]:
            timeout = float(kwargs.pop("timeout"))  # type: ignore[arg-type]
            with ExitStack() as stack:
                client = stack.enter_context(
                    build_client(timeout_s=timeout, transport=self._transport)
                )
                response = stack.enter_context(
                    client.stream(request_method, request_url, **kwargs)  # type: ignore[arg-type]
                )
                yield response

        with safe_http_stream(
            url,
            timeout=self._base_timeout(),
            headers=dict(headers),
            resolver=self._dns_resolver,
            stream_factory=factory,
            method=method,
            content=content,
        ) as response:
            yield response

    def _base_timeout(self) -> float:
        if not self._routes:
            return 60.0
        return max(route.request_timeout_s for route in self._routes.values())

    def _request_adapter(
        self,
        *,
        client: httpx.Client,
        method: str,
        path: str,
        body_spec: object,
        config: AsyncJobRouteConfig,
        upload_path: Path | None,
        provider: str,
        ctx: TemplateContext,
    ) -> object:
        headers = base_headers(self._extra_headers)
        self._auth.apply(headers)
        return self._send(
            client=client,
            method=method,
            url=f"{self._base_url}{path}",
            headers=headers,
            body_spec=body_spec,
            config=config,
            upload_path=upload_path,
            provider=provider,
            ctx=ctx,
        )

    def _request_no_auth(
        self,
        *,
        client: httpx.Client,
        method: str,
        url: str,
        body_spec: object,
        config: AsyncJobRouteConfig,
        upload_path: Path | None,
        provider: str,
        ctx: TemplateContext,
    ) -> object:
        # auth: none — a captured URL. No adapter credentials and no extra headers
        # are ever sent here (Section 2.10). Only accept is set.
        headers = {"accept": "application/json"}
        return self._send(
            client=client,
            method=method,
            url=url,
            headers=headers,
            body_spec=body_spec,
            config=config,
            upload_path=upload_path,
            provider=provider,
            ctx=ctx,
            expect_json=False,
            pinned=True,
        )

    def _send(
        self,
        *,
        client: httpx.Client,
        method: str,
        url: str,
        headers: dict[str, str],
        body_spec: object,
        config: AsyncJobRouteConfig,
        upload_path: Path | None,
        provider: str,
        ctx: TemplateContext,
        expect_json: bool = True,
        pinned: bool = False,
    ) -> object:
        json_body: object | None = None
        upload_file: Path | None = None
        if body_spec == "input.file":
            if upload_path is None:
                raise self._rejected(provider, "no prepared upload file")
            upload_file = upload_path
            headers["content-type"] = "application/octet-stream"
            if config.upload_filename:
                headers["content-disposition"] = (
                    f'attachment; filename="{config.upload_filename}"'
                )
        elif body_spec is not None:
            try:
                json_body = evaluate_value(body_spec, ctx)
            except TemplateMissing as exc:
                raise self._malformed(provider) from exc
            except TemplateError as exc:
                raise self._rejected(provider, str(exc)) from exc
        try:
            with ExitStack() as stack:
                # The upload is streamed from disk, as the archived adapters do,
                # instead of being read into memory.
                content: object = (
                    stack.enter_context(upload_file.open("rb"))
                    if upload_file is not None
                    else None
                )
                if pinned:
                    if json_body is not None:
                        content = json.dumps(json_body).encode("utf-8")
                        headers["content-type"] = "application/json"
                    response = stack.enter_context(
                        self._pinned_stream(url, method=method, headers=headers, content=content)
                    )
                    response.read()
                else:
                    response = client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body if json_body is not None else None,
                        content=content,  # type: ignore[arg-type]
                    )
        except OSError as exc:
            # The prepared input could not be read: a local failure, nothing sent.
            raise self._rejected(provider, "prepared input could not be read") from exc
        except MediaUrlPolicyError as exc:
            raise self._malformed(provider) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider=provider, error_info=classify_provider_exception(exc)
            ) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=provider,
                error_info=classify_provider_status(response.status_code),
            )
        if not expect_json:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._malformed(provider) from exc
        if not isinstance(payload, (Mapping, list)):
            raise self._malformed(provider)
        return payload

    # -- template plumbing --------------------------------------------------
    def _ctx(
        self, vars_state: Mapping[str, object], params: Mapping[str, object]
    ) -> TemplateContext:
        return TemplateContext(params=dict(params), vars=dict(vars_state))

    def _initial_vars(
        self, mapping: Mapping[str, object], config: AsyncJobRouteConfig, provider: str
    ) -> dict[str, object]:
        vars_block = mapping.get("vars")
        if not isinstance(vars_block, Mapping):
            return {}
        ctx = self._ctx({}, config.params)
        out: dict[str, object] = {}
        for name, spec in vars_block.items():
            try:
                out[str(name)] = evaluate_value(spec, ctx)
            except TemplateError as exc:
                raise self._rejected(provider, f"vars.{name}: {exc}") from exc
        return out

    def _render_path(self, path: str, ctx: TemplateContext, provider: str) -> str:
        try:
            return render_path(path, ctx)
        except TemplateMissing as exc:
            raise self._malformed(provider) from exc
        except TemplateError as exc:
            raise self._rejected(provider, str(exc)) from exc

    def _capture(
        self,
        step: Mapping[str, object],
        payload: object,
        vars_state: dict[str, object],
        provider: str,
    ) -> None:
        capture = step.get("capture")
        if not isinstance(capture, Mapping):
            return
        ctx = TemplateContext(response=payload)
        for name, path in capture.items():
            try:
                vars_state[str(name)] = resolve_ref(str(path), ctx)
            except TemplateMissing as exc:
                raise self._malformed(provider) from exc

    def _check_requirements(
        self, step: Mapping[str, object], vars_state: Mapping[str, object], provider: str
    ) -> None:
        require = step.get("require")
        if not isinstance(require, Mapping):
            return
        for name, predicate in require.items():
            value = vars_state.get(str(name))
            if predicate == "https":
                if not isinstance(value, str) or urlsplit(value).scheme != "https":
                    raise self._malformed(provider)

    @staticmethod
    def _requires_https(step: Mapping[str, object]) -> bool:
        require = step.get("require")
        if not isinstance(require, Mapping):
            return False
        return any(predicate == "https" for predicate in require.values())

    def _resolve_captured_url(
        self, url_ref: str, vars_state: Mapping[str, object], *, require_https: bool
    ) -> str:
        # ``url`` is a bare $ref to a captured var (Section 2.10 shorthand).
        ctx = TemplateContext(vars=dict(vars_state))
        try:
            value = resolve_ref(url_ref, ctx)
        except TemplateMissing as exc:
            raise self._malformed(self._provider or "provider") from exc
        if not isinstance(value, str) or not value:
            raise self._malformed(self._provider or "provider")
        parsed = urlsplit(value)
        # No userinfo, and https when required; the SSRF policy does the rest.
        if parsed.username is not None or parsed.password is not None:
            raise self._malformed(self._provider or "provider")
        if require_https and parsed.scheme.lower() != "https":
            raise self._malformed(self._provider or "provider")
        try:
            validate_outbound_media_url(value, resolver=self._resolver())
        except MediaUrlPolicyError as exc:
            raise self._malformed(self._provider or "provider") from exc
        return value

    def _resolver(self):  # noqa: ANN202 - matches media_url_policy's Resolver type
        return self._dns_resolver

    def _commit_operation_id(
        self, step: Mapping[str, object], vars_state: Mapping[str, object]
    ) -> str | None:
        # The provider operation id is the LAST capture of the commit step
        # (documented route-level rule). Fall back to None when there is none.
        capture = step.get("capture")
        if not isinstance(capture, Mapping) or not capture:
            return None
        last_name = list(capture.keys())[-1]
        value = vars_state.get(str(last_name))
        return str(value) if value is not None else None

    # -- input transform ----------------------------------------------------
    def _prepare_input(
        self,
        request: DiarizationRequest | StemSeparationRequest,
        config: AsyncJobRouteConfig,
        temp_dir: Path,
    ) -> Path:
        source = (
            request.audio_path
            if isinstance(request, DiarizationRequest)
            else request.source_path
        )
        if config.input_transform_audio is None:
            return source
        return self._transform_audio(source, config.input_transform_audio, temp_dir)

    def _transform_audio(
        self, source: Path, transform: AudioTransform, temp_dir: Path
    ) -> Path:
        # Mirror extract_flac_audio's command (audio-only rendition) with the
        # route-configured codec/sample-rate/channels, through media_tool_env().
        output = temp_dir / f"source-audio.{transform.codec}"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(transform.channels),
            "-ar",
            str(transform.sample_rate),
            "-c:a",
            transform.codec,
            str(output),
        ]
        try:
            result = self._audio_runner(command)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise self._rejected(
                self._provider or "provider",
                "The source audio could not be prepared.",
            ) from exc
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            raise self._rejected(
                self._provider or "provider", "The source has no usable audio."
            )
        return output

    # -- usage --------------------------------------------------------------
    @staticmethod
    def _diarization_units(route: ModelRoute, request: DiarizationRequest) -> int:
        # Diarization bills seconds (as today): max(duration, route minimum). The
        # per-job minimum now comes from the route (Task 17); a request-level
        # minimum still acts as a floor for the compatibility path.
        return diarization_billable_seconds(
            request, minimum_billable_units=route.minimum_billable_units
        )

    @staticmethod
    def _stem_units(route: ModelRoute, request: StemSeparationRequest) -> int:
        # Stems bill duration units (stem_duration_units), never below the route
        # minimum. A missing duration bills the minimum (at least one unit).
        duration = request.duration_s or float(max(1, route.minimum_billable_units))
        return max(stem_duration_units(duration), route.minimum_billable_units)

    # -- utilities ----------------------------------------------------------
    @staticmethod
    def _field_value(spec: object, ctx: TemplateContext) -> object:
        # A bare string is a $ref relative to the current item; a mapping/list is
        # a full value form (so to_ms etc. can convert an extracted value).
        if isinstance(spec, str):
            return resolve_relative_ref(spec, ctx)
        return evaluate_value(spec, ctx)

    def _resolve_items(
        self,
        path: str,
        response: object,
        provider: str,
        vars_state: Mapping[str, object] | None = None,
    ) -> list[object]:
        # ``vars`` is threaded so an items path keyed by a captured id (for
        # example ``response.result.{vars.task_id}.result.tracks``) resolves.
        ctx = TemplateContext(response=response, vars=dict(vars_state or {}))
        try:
            items = resolve_ref(path, ctx)
        except TemplateMissing as exc:
            raise self._malformed(provider) from exc
        if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
            raise self._malformed(provider)
        return list(items)

    def _malformed(self, provider: str) -> ProviderCallFailure:
        return ProviderCallFailure(
            provider=provider,
            error_info=ProviderErrorInfo(
                "provider_malformed_response",
                False,
                fallback_eligible=True,
                circuit_worthy=True,
            ),
        )

    def _rejected(self, provider: str, message: str) -> ProviderCallFailure:
        # Every caller fails locally, before a request is sent.
        return ProviderCallFailure(
            provider=provider,
            error_info=ProviderErrorInfo(
                "provider_request_rejected",
                False,
                fallback_eligible=False,
                circuit_worthy=False,
                billable=False,
                sent=False,
            ),
            message=message,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        try:
            return UUID(value)
        except ValueError:
            pass
    # A deterministic sentinel namespace for callers that do not carry a tenant
    # (local/dev). Task 17 wires real workspace/project ids from the caller.
    import uuid as _uuid

    return _uuid.uuid5(_uuid.NAMESPACE_URL, f"motionly-async-local:{value!r}")


def _fresh_key() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex


def _resume_vars(vars_state: Mapping[str, object]) -> dict[str, object]:
    # Persist only JSON-scalar captures (ids, URLs). Drop bytes and nested
    # structures; they are not needed to resume polling or cleanup.
    out: dict[str, object] = {}
    for name, value in vars_state.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[str(name)] = value
    return out


def _referenced_vars(body_spec: object) -> set[str]:
    names: set[str] = set()
    if isinstance(body_spec, Mapping):
        if "$ref" in body_spec and isinstance(body_spec["$ref"], str):
            ref = body_spec["$ref"]
            if ref.startswith("vars."):
                names.add(ref.split(".", 1)[1].split(".", 1)[0].split("[", 1)[0])
        for value in body_spec.values():
            names |= _referenced_vars(value)
    elif isinstance(body_spec, list):
        for item in body_spec:
            names |= _referenced_vars(item)
    return names


def _path_vars(path: str) -> set[str]:
    import re

    return {match.group(1) for match in re.finditer(r"\{vars\.([A-Za-z_][A-Za-z0-9_]*)\}", path)}
