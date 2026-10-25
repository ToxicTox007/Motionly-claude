"""Amazon Bedrock ``async-invoke`` video generation runner (spec item D).

Text-to-video generation on Bedrock is asynchronous: the caller submits a job
with ``StartAsyncInvoke`` (``POST /async-invoke``), Bedrock writes the finished
MP4 to a caller-owned S3 prefix, and the caller polls ``GetAsyncInvoke``
(``GET /async-invoke/{invocationArn}``) until the status flips from
``InProgress`` to ``Completed`` or ``Failed``.

This module turns that flow into the two typed outputs the gateway caller in
:mod:`app.services.scale` consumes:

* :meth:`AsyncInvokeVideoRunner.start_video_job` returns a
  :class:`~app.services.model_gateway.GenerationOperationOutput` whose
  ``operation_id`` is the ``invocationArn``.
* :meth:`AsyncInvokeVideoRunner.check_video_job` returns another
  ``GenerationOperationOutput`` while the job is still ``InProgress`` and a
  :class:`~app.services.model_gateway.GenerationOutput` with a single presigned
  ``https://`` artifact URL once it is ``Completed``. A ``Failed`` job raises a
  non-retryable, non-billable :class:`ProviderCallFailure`.

Two model families are supported through a pure data table,
:data:`VIDEO_MODEL_FAMILIES`:

* ``nova_reel`` — Amazon Nova Reel. Durations up to six seconds map to the
  single-shot ``TEXT_VIDEO`` task; longer durations map to
  ``MULTI_SHOT_AUTOMATED`` with the duration rounded up to a six-second multiple
  and clamped to ``12..120``. ``dimension`` is ``1280x720`` at 24 fps with a
  deterministic ``seed`` derived from the idempotency hash.
* ``luma_ray`` — Luma Ray 2. The ``768:1280`` ratio maps to ``9:16`` and
  ``1280:768`` to ``16:9``; duration is ``5s`` for short requests and ``9s``
  otherwise; ``resolution`` defaults to ``720p``; ``loop`` is false.

Both families are text-to-video only, so ``prompt_image_url`` is never sent; it
is recorded under ``ignored`` in ``raw_response`` for observability.

The module is additive and dependency-free: standard library plus ``httpx`` and
the existing :mod:`app.services.aws_sigv4` signer. It never makes a real network
call on its own — the HTTP transport is injected (``httpx.MockTransport`` in
tests). Credentials are resolved lazily through an injected chain exposing
``resolve() -> SigV4Credentials`` and are never logged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.services.aws_s3_objects import S3ObjectStore, parse_s3_uri
from app.services.aws_sigv4 import SigV4Credentials, sign_request
from app.services.inference_http import (
    build_client,
    exception_failure,
    read_json_response,
    status_error_info,
)
from app.services.model_gateway import (
    GeneratedArtifact,
    GenerationOperationOutput,
    GenerationOutput,
    VideoGenerationSubmitRequest,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

# The SigV4 service name for the ``bedrock-runtime`` endpoint (shared with the
# Converse / InvokeModel adapter).
BEDROCK_RUNTIME_SERVICE = "bedrock"

# Nova Reel seed bound: the AWS doc allows 0..2,147,483,646 inclusive.
_NOVA_SEED_MODULUS = 2_147_483_647

# Nova Reel task names and their prompt character limits.
_NOVA_TEXT_VIDEO = "TEXT_VIDEO"
_NOVA_MULTI_SHOT = "MULTI_SHOT_AUTOMATED"
_NOVA_TEXT_VIDEO_PROMPT_LIMIT = 512
_NOVA_MULTI_SHOT_PROMPT_LIMIT = 4000

# A Bedrock async-invoke ARN: ``arn:aws:bedrock:<region>:<account>:async-invoke/<id>``.
_INVOCATION_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[a-z0-9-]+:\d{12}:async-invoke/[A-Za-z0-9._-]+$"
)

# Bounded cap on the JSON status / submit response body.
_MAX_RESPONSE_BYTES = 256 * 1024


class VideoFamilyError(ValueError):
    """Raised when an unknown video model family is requested."""


def _idempotency_hash(idempotency_key: str) -> str:
    """Deterministic 64-char client-request token from the idempotency key."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:64]


def _nova_seed(idempotency_key: str) -> int:
    """A stable Nova seed in ``0..2147483646`` derived from the idempotency key.

    Using the idempotency hash keeps a retry of the same request deterministic
    while varying across distinct requests.
    """
    digest = hashlib.sha256(f"seed:{idempotency_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % _NOVA_SEED_MODULUS


def _rejected_failure(provider: str, message: str) -> ProviderCallFailure:
    """A request rejected before anything was sent: not billed, no fallback."""
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


@dataclass(frozen=True)
class VideoFamilyPlan:
    """The resolved per-request plan produced by a :class:`VideoModelFamily`."""

    model_input: Mapping[str, object]
    duration_s: int
    task: str


class VideoModelFamily:
    """Base class for a video model family's request planning."""

    name: str

    def plan(
        self,
        provider: str,
        request: VideoGenerationSubmitRequest,
        *,
        default_resolution: str,
    ) -> VideoFamilyPlan:  # pragma: no cover - abstract
        raise NotImplementedError


class _NovaReelFamily(VideoModelFamily):
    """Amazon Nova Reel text-to-video planning."""

    name = "nova_reel"

    def plan(
        self,
        provider: str,
        request: VideoGenerationSubmitRequest,
        *,
        default_resolution: str,
    ) -> VideoFamilyPlan:
        if request.duration_s <= 6:
            task = _NOVA_TEXT_VIDEO
            duration = 6
            prompt_limit = _NOVA_TEXT_VIDEO_PROMPT_LIMIT
        else:
            task = _NOVA_MULTI_SHOT
            # Round up to the next six-second multiple, clamp to 12..120.
            duration = min(120, max(12, math.ceil(request.duration_s / 6) * 6))
            prompt_limit = _NOVA_MULTI_SHOT_PROMPT_LIMIT
        if len(request.prompt) > prompt_limit:
            raise _rejected_failure(
                provider,
                f"prompt exceeds the {task} limit of {prompt_limit} characters",
            )
        config = {
            "durationSeconds": duration,
            "fps": 24,
            "dimension": "1280x720",
            "seed": _nova_seed(request.idempotency_key),
        }
        if task == _NOVA_TEXT_VIDEO:
            model_input: dict[str, object] = {
                "taskType": _NOVA_TEXT_VIDEO,
                "textToVideoParams": {"text": request.prompt},
                "videoGenerationConfig": config,
            }
        else:
            model_input = {
                "taskType": _NOVA_MULTI_SHOT,
                "multiShotAutomatedParams": {"text": request.prompt},
                "videoGenerationConfig": config,
            }
        return VideoFamilyPlan(model_input=model_input, duration_s=duration, task=task)


class _LumaRayFamily(VideoModelFamily):
    """Luma Ray 2 text-to-video planning."""

    name = "luma_ray"

    _ASPECT_BY_RATIO = {"768:1280": "9:16", "1280:768": "16:9"}

    def plan(
        self,
        provider: str,
        request: VideoGenerationSubmitRequest,
        *,
        default_resolution: str,
    ) -> VideoFamilyPlan:
        aspect = self._ASPECT_BY_RATIO.get(request.ratio)
        if aspect is None:
            raise _rejected_failure(
                provider, f"unsupported ratio for luma_ray: {request.ratio!r}"
            )
        duration_label = "5s" if request.duration_s <= 7 else "9s"
        numeric_duration = 5 if duration_label == "5s" else 9
        model_input = {
            "prompt": request.prompt,
            "aspect_ratio": aspect,
            "duration": duration_label,
            "resolution": default_resolution or "720p",
            "loop": False,
        }
        return VideoFamilyPlan(
            model_input=model_input, duration_s=numeric_duration, task="text_to_video"
        )


# The pure family data table, keyed by the config ``video_family`` value.
VIDEO_MODEL_FAMILIES: Mapping[str, VideoModelFamily] = {
    _NovaReelFamily.name: _NovaReelFamily(),
    _LumaRayFamily.name: _LumaRayFamily(),
}


def resolve_video_family(family: str) -> VideoModelFamily:
    """Return the :class:`VideoModelFamily` for ``family`` or raise."""
    resolved = VIDEO_MODEL_FAMILIES.get(family)
    if resolved is None:
        raise VideoFamilyError(f"unknown video model family: {family!r}")
    return resolved


def validate_invocation_arn(invocation_arn: str) -> bool:
    """Whether ``invocation_arn`` has the Bedrock ``async-invoke`` ARN shape."""
    return bool(_INVOCATION_ARN_RE.match(invocation_arn))


class AsyncInvokeVideoRunner:
    """Drive one Bedrock ``async-invoke`` video model family.

    ``store`` is the :class:`~app.services.aws_s3_objects.S3ObjectStore` for the
    *output* bucket; the runner lists the completed job's objects and mints the
    presigned artifact URL through it. ``credentials`` is any object exposing
    ``resolve() -> SigV4Credentials`` (the shared credential chain). ``transport``
    injects ``httpx.MockTransport`` in tests and ``clock`` supplies the signing
    timestamp so signing stays deterministic.
    """

    def __init__(
        self,
        *,
        region: str,
        family: str,
        store: S3ObjectStore,
        output_prefix: str,
        provider: str,
        credentials: object,
        presign_expires_s: int = 900,
        video_resolution: str = "720p",
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_s: float = 60.0,
    ) -> None:
        self._region = region
        self._family_name = family
        self._family = resolve_video_family(family)
        self._store = store
        self._output_prefix = output_prefix.strip("/")
        self._provider = provider
        self._credentials = credentials
        self._presign_expires_s = presign_expires_s
        self._video_resolution = video_resolution
        self._transport = transport
        self._clock = clock
        self._timeout_s = timeout_s
        self._host = f"bedrock-runtime.{region}.amazonaws.com"

    @property
    def host(self) -> str:
        return self._host

    @property
    def family(self) -> str:
        return self._family_name

    # -- credential resolution ---------------------------------------------
    def _resolve_bedrock_credentials(self) -> SigV4Credentials:
        try:
            return self._credentials.resolve()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - mapped to a provider failure
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=ProviderErrorInfo(
                    "provider_auth_failed",
                    False,
                    fallback_eligible=True,
                    circuit_worthy=True,
                ),
                message=str(exc) or None,
            ) from exc

    def _amz_date(self) -> str:
        return self._clock().strftime("%Y%m%dT%H%M%SZ")

    def _output_s3_uri(self) -> str:
        key = self._output_prefix
        return f"s3://{self._store.bucket}/{key}" if key else f"s3://{self._store.bucket}"

    def _send_async_invoke(
        self,
        *,
        method: str,
        path: str,
        payload: bytes = b"",
    ) -> object:
        """Sign and send one request to the ``bedrock-runtime`` endpoint."""
        credentials = self._resolve_bedrock_credentials()
        headers: dict[str, str] = {}
        if payload:
            headers["content-type"] = "application/json"
        signed = sign_request(
            credentials=credentials,
            method=method,
            host=self._host,
            path=path,
            region=self._region,
            service=BEDROCK_RUNTIME_SERVICE,
            amz_date=self._amz_date(),
            headers=headers,
            payload=payload,
        )
        url = f"https://{self._host}{path}"
        try:
            with build_client(
                timeout_s=self._timeout_s, transport=self._transport
            ) as client:
                response = client.request(
                    method, url, headers=dict(signed.headers), content=payload
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(self._provider, exc) from None
        return read_json_response(
            response, provider=self._provider, max_bytes=_MAX_RESPONSE_BYTES
        )

    # -- submit -------------------------------------------------------------
    def start_video_job(
        self, model_id: str, request: VideoGenerationSubmitRequest
    ) -> GenerationOperationOutput:
        """Submit a ``StartAsyncInvoke`` job and return its ``invocationArn``.

        ``prompt_image_url`` is intentionally not sent (text-to-video only) and
        is recorded under ``raw_response['ignored']``.
        """
        plan = self._family.plan(
            self._provider, request, default_resolution=self._video_resolution
        )
        body: dict[str, object] = {
            "clientRequestToken": _idempotency_hash(request.idempotency_key),
            "modelId": model_id,
            "modelInput": plan.model_input,
            "outputDataConfig": {
                "s3OutputDataConfig": {"s3Uri": self._output_s3_uri()}
            },
        }
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        parsed = self._send_async_invoke(
            method="POST", path="/async-invoke", payload=payload
        )
        if not isinstance(parsed, Mapping):
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        invocation_arn = parsed.get("invocationArn")
        if not isinstance(invocation_arn, str) or not validate_invocation_arn(
            invocation_arn
        ):
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        ignored: dict[str, object] = {}
        if request.prompt_image_url:
            ignored["prompt_image_url"] = request.prompt_image_url
        raw_response: dict[str, object] = {
            "family": self._family_name,
            "task": plan.task,
            "duration_s": plan.duration_s,
        }
        if ignored:
            raw_response["ignored"] = ignored
        return GenerationOperationOutput(
            operation_id=invocation_arn, raw_response=raw_response
        )

    # -- poll ---------------------------------------------------------------
    def check_video_job(
        self, invocation_arn: str
    ) -> GenerationOperationOutput | GenerationOutput:
        """Poll ``GetAsyncInvoke`` once and translate the status.

        ``InProgress`` -> :class:`GenerationOperationOutput` (still running).
        ``Completed`` -> :class:`GenerationOutput` with a presigned artifact URL.
        ``Failed`` -> non-retryable, non-billable :class:`ProviderCallFailure`.
        """
        if not validate_invocation_arn(invocation_arn):
            raise _rejected_failure(
                self._provider, f"malformed invocation ARN: {invocation_arn!r}"
            )
        # The ARN is sent singly-encoded in the path and signed exactly as sent.
        path = f"/async-invoke/{quote(invocation_arn, safe='')}"
        parsed = self._send_async_invoke(method="GET", path=path)
        if not isinstance(parsed, Mapping):
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        status = parsed.get("status")
        if status == "InProgress":
            return GenerationOperationOutput(
                operation_id=invocation_arn,
                raw_response={"status": "InProgress"},
            )
        if status == "Failed":
            message = parsed.get("failureMessage")
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=ProviderErrorInfo(
                    "provider_request_rejected",
                    False,
                    fallback_eligible=False,
                    circuit_worthy=False,
                    billable=False,
                    sent=True,
                ),
                message=message if isinstance(message, str) and message else None,
            )
        if status != "Completed":
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        return self._completed_output(invocation_arn, parsed)

    def _completed_output(
        self, invocation_arn: str, parsed: Mapping[str, object]
    ) -> GenerationOutput:
        output_uri = self._output_uri_from_status(parsed)
        location = parse_s3_uri(output_uri)
        prefix = location.key.rstrip("/")
        list_prefix = f"{prefix}/" if prefix else ""
        keys = self._store.list_object_keys(list_prefix, max_keys=100)
        artifact_key = self._select_mp4_key(keys)
        if artifact_key is None:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        url = self._store.presigned_get_url(
            artifact_key, expires_s=self._presign_expires_s
        )
        return GenerationOutput(
            artifacts=(GeneratedArtifact(url=url),),
            operation_id=invocation_arn,
            raw_response={"status": "Completed", "artifact_key": artifact_key},
        )

    def _output_uri_from_status(self, parsed: Mapping[str, object]) -> str:
        config = parsed.get("outputDataConfig")
        if isinstance(config, Mapping):
            s3_config = config.get("s3OutputDataConfig")
            if isinstance(s3_config, Mapping):
                uri = s3_config.get("s3Uri")
                if isinstance(uri, str) and uri.startswith("s3://"):
                    return uri
        # Fall back to the submit-time output prefix when the status omits it.
        return self._output_s3_uri()

    @staticmethod
    def _select_mp4_key(keys: list[str]) -> str | None:
        """Prefer a key ending ``output.mp4``; otherwise the first ``.mp4`` key."""
        mp4_keys = [key for key in keys if key.endswith(".mp4")]
        if not mp4_keys:
            return None
        for key in mp4_keys:
            if key.endswith("output.mp4"):
                return key
        return mp4_keys[0]
