"""Amazon Bedrock Data Automation (BDA) audio runner (plan item C).

Transcription (``transcription.speech_to_text``) and diarization
(``audio.diarization``) on a CPU-only deployment are served by Bedrock Data
Automation: the audio is staged to S3, an asynchronous BDA job is invoked, the
job is polled to completion, and the structured audio *standard output* is read
back from S3 and mapped onto the neutral :class:`TranscriptionOutput` /
:class:`DiarizationOutput` contracts.

This module owns only the BDA control flow; all S3 traffic goes through the
shared :class:`~app.services.aws_s3_objects.S3ObjectStore` and all SigV4 signing
through :func:`~app.services.aws_sigv4.sign_request`. It is additive and
dependency-free (standard library plus ``httpx`` plus the existing helpers).

API shape (confirmed against the botocore model
``bedrock-data-automation-runtime/2024-06-13``): JSON 1.1 protocol, signing name
``bedrock``, target prefix ``AmazonBedrockKeystoneRuntimeService``, request URI
``/`` on host ``bedrock-data-automation-runtime.<region>.amazonaws.com``.

* ``InvokeDataAutomationAsync`` (idempotent via ``clientToken``) returns an
  ``invocationArn``.
* ``GetDataAutomationStatus`` returns a ``status`` of ``Created`` / ``InProgress``
  / ``Success`` / ``ServiceError`` / ``ClientError`` plus an
  ``outputConfiguration.s3Uri`` (the ``job_metadata.json``).

The audio *standard output* shape (confirmed against the Bedrock user guide) is:
``metadata`` (``dominant_asset_language``, ``duration_millis``), ``audio_items``
(``content`` + ``start_timestamp_millis`` / ``end_timestamp_millis``) and
``audio_segments`` (``type`` ``TRANSCRIPT``, ``text``, ``speaker.speaker_label``,
timestamps). Speaker labels require the operator to enable speaker labeling in
the BDA project.

"AWS"/"Bedrock" may be named here (not on the vendor-neutrality scanner's
audited list); the Motionly core never names them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.services.aws_s3_objects import S3ObjectStore, parse_s3_uri, safe_object_key
from app.services.aws_sigv4 import SigV4Credentials, sign_request
from app.services.inference_http import (
    build_client,
    error_info_for_class,
    exception_failure,
    status_error_info,
)
from app.services.inference_normalization import sort_diarization_segments
from app.services.model_gateway import (
    DiarizationOutput,
    DiarizationSegmentOutput,
    TranscriptionOutput,
    TranscriptWordOutput,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

# The BDA runtime shares the ``bedrock`` SigV4 signing name (botocore model).
BEDROCK_DATA_AUTOMATION_SERVICE = "bedrock"

# JSON-1.1 target prefix and content type (botocore ``targetPrefix`` + protocol).
_TARGET_PREFIX = "AmazonBedrockKeystoneRuntimeService"
_JSON_CONTENT_TYPE = "application/x-amz-json-1.1"

# Audio formats Bedrock Data Automation accepts. The transcription/diarization
# callers stage mono ``.wav`` (``extract_mono_wav``); the common upload codecs
# are accepted too so a route can skip re-encoding when the source already fits.
SUPPORTED_AUDIO_SUFFIXES: frozenset[str] = frozenset(
    {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".amr", ".webm", ".aac"}
)

# Caps for the two JSON artifacts read back from S3 (defensive, bounded reads).
_METADATA_MAX_BYTES = 1 * 1024 * 1024
_RESULT_MAX_BYTES = 16 * 1024 * 1024

# A data-automation profile ARN, e.g.
# ``arn:aws:bedrock:ap-south-1:123456789012:data-automation-profile/...``.
_PROFILE_ARN_RE = re.compile(
    r"^arn:aws[a-z0-9-]*:bedrock:[a-z0-9-]+:(?:aws|[0-9]{12}):"
    r"data-automation-profile/[A-Za-z0-9\-_.]+$"
)
# A data-automation project ARN (speaker labeling is configured on the project).
_PROJECT_ARN_RE = re.compile(
    r"^arn:aws[a-z0-9-]*:bedrock:[a-z0-9-]+:(?:aws|[0-9]{12}):"
    r"data-automation-project/[A-Za-z0-9\-_]+$"
)


class DataAutomationOutputError(Exception):
    """BDA standard output could not be mapped to a neutral contract.

    Raised by the result mappers; the adapter turns it into the ``malformed``
    provider error class. The most common operator-fixable cause is a missing
    ``speaker_label`` (speaker labeling not enabled on the BDA project).
    """


def validate_data_automation_arns(
    *, profile_arn: str, project_arn: str | None
) -> list[str]:
    """Return human-readable problems with the BDA ARNs (empty = valid).

    The profile ARN is always required; the project ARN is optional but, when
    present, must match the ``data-automation-project`` shape. Never resolves a
    credential and never echoes a secret.
    """
    errors: list[str] = []
    if not profile_arn or not _PROFILE_ARN_RE.match(profile_arn):
        errors.append("data_automation_profile_arn must be a data-automation-profile ARN")
    if project_arn is not None and not _PROJECT_ARN_RE.match(project_arn):
        errors.append("data_automation_project_arn must be a data-automation-project ARN")
    return errors


@dataclass(frozen=True)
class DataAutomationSettings:
    """Configuration for one BDA audio runner.

    ``region`` fixes the runtime host. ``profile_arn`` is required; ``project_arn``
    selects a project (required for diarization so speaker labeling is enabled).
    ``s3_bucket`` / ``s3_prefix`` / ``s3_region`` locate the staging area. Polling
    backs off from ``poll_interval_s`` by x1.5 up to ``max_poll_interval_s`` until
    ``max_wait_s`` elapses. ``cleanup`` best-effort removes staged objects.
    """

    region: str
    profile_arn: str
    s3_bucket: str
    s3_prefix: str
    s3_region: str
    project_arn: str | None = None
    project_stage: str = "LIVE"
    max_wait_s: float = 900.0
    poll_interval_s: float = 5.0
    max_poll_interval_s: float = 30.0
    cleanup: bool = True
    timeout_s: float = 60.0

    @property
    def host(self) -> str:
        return f"bedrock-data-automation-runtime.{self.region}.amazonaws.com"


def _rejected_failure(provider: str, message: str) -> ProviderCallFailure:
    """A client-side rejection: not retryable, not billed, no fallback, not sent."""
    info = error_info_for_class("rejected")
    return ProviderCallFailure(
        provider=provider,
        error_info=ProviderErrorInfo(
            info.code,
            info.retryable,
            info.status_code,
            fallback_eligible=info.fallback_eligible,
            circuit_worthy=info.circuit_worthy,
            billable=False,
            sent=False,
        ),
        message=message,
    )


def _timeout_failure(provider: str, message: str) -> ProviderCallFailure:
    """A deadline failure after the job was submitted (``sent=True``)."""
    return ProviderCallFailure(
        provider=provider,
        error_info=ProviderErrorInfo(
            "provider_unavailable",
            True,
            None,
            billable=False,
            sent=True,
        ),
        message=message,
    )


def _malformed_output_failure(provider: str, message: str) -> ProviderCallFailure:
    """BDA output that cannot be mapped -> the standard malformed class."""
    info = error_info_for_class("malformed")
    return ProviderCallFailure(
        provider=provider,
        error_info=info,
        message=message,
    )


class DataAutomationAudioRunner:
    """Run a BDA audio job end-to-end: stage, invoke, poll, read, clean up.

    ``credentials`` is any object exposing ``resolve() -> SigV4Credentials`` (the
    shared :class:`~app.services.aws_bedrock_adapter.IamCredentialChain`).
    ``store`` is the :class:`S3ObjectStore` for the staging bucket. ``transport``
    is the ``httpx.MockTransport`` injection point in tests; ``clock`` supplies
    the signing timestamp; ``sleep`` / ``monotonic`` drive the poll loop and are
    injected so tests never really sleep.
    """

    def __init__(
        self,
        settings: DataAutomationSettings,
        *,
        credentials: object,
        provider: str,
        store: S3ObjectStore,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._credentials = credentials
        self._provider = provider
        self._store = store
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic

    # -- public entry point -------------------------------------------------
    def run_audio_analysis(
        self, audio_path: Path, *, idempotency_scope: str
    ) -> Mapping[str, object]:
        """Stage ``audio_path``, run a BDA job, and return the parsed result.

        The returned mapping is the BDA audio standard output (``metadata``,
        ``audio_items``, ``audio_segments``), ready for
        :func:`transcription_from_result` / :func:`diarization_from_result`.
        """
        suffix = audio_path.suffix.lower()
        if suffix not in SUPPORTED_AUDIO_SUFFIXES:
            # Reject before any upload or invoke so an unsupported file is never
            # billed (rejected class, provably not sent).
            raise _rejected_failure(
                self._provider,
                f"unsupported audio format for Bedrock Data Automation: {suffix!r}",
            )
        audio_bytes = audio_path.read_bytes()
        token = self._job_token(idempotency_scope, audio_bytes)
        prefix = self._settings.s3_prefix

        input_key = safe_object_key(prefix, "input", token + suffix)
        output_prefix = safe_object_key(prefix, "output", token)
        self._store.put_object_bytes(
            input_key, audio_bytes, content_type="application/octet-stream"
        )

        invocation_arn = self._invoke_async(
            input_s3_uri=self._store.s3_uri(input_key),
            output_s3_uri=self._store.s3_uri(output_prefix),
            client_token=token,
        )
        try:
            metadata_s3_uri = self._poll_until_done(invocation_arn)
            result = self._read_standard_output(metadata_s3_uri)
        finally:
            if self._settings.cleanup:
                self._cleanup_objects(input_key, output_prefix)
        return result

    # -- idempotency --------------------------------------------------------
    def _job_token(self, scope: str, audio_bytes: bytes) -> str:
        """Deterministic clientToken so a retry reuses the same BDA job.

        ``sha256(f"{scope}:{sha256(audio)}")`` keeps the token stable for the same
        audio and scope while never colliding across different inputs.
        """
        audio_digest = hashlib.sha256(audio_bytes).hexdigest()
        combined = f"{scope}:{audio_digest}".encode()
        return hashlib.sha256(combined).hexdigest()[:64]

    # -- invoke -------------------------------------------------------------
    def _invoke_async(
        self, *, input_s3_uri: str, output_s3_uri: str, client_token: str
    ) -> str:
        body: dict[str, object] = {
            "clientToken": client_token,
            "inputConfiguration": {"s3Uri": input_s3_uri},
            "outputConfiguration": {"s3Uri": output_s3_uri},
            "dataAutomationProfileArn": self._settings.profile_arn,
        }
        if self._settings.project_arn:
            body["dataAutomationConfiguration"] = {
                "dataAutomationProjectArn": self._settings.project_arn,
                "stage": self._settings.project_stage,
            }
        payload = self._post_json("InvokeDataAutomationAsync", body)
        arn = payload.get("invocationArn") if isinstance(payload, Mapping) else None
        if not isinstance(arn, str) or not arn:
            raise _malformed_output_failure(
                self._provider, "InvokeDataAutomationAsync returned no invocationArn"
            )
        return arn

    # -- poll ---------------------------------------------------------------
    def _poll_until_done(self, invocation_arn: str) -> str:
        deadline = self._monotonic() + self._settings.max_wait_s
        interval = self._settings.poll_interval_s
        while True:
            payload = self._post_json(
                "GetDataAutomationStatus", {"invocationArn": invocation_arn}
            )
            status = (
                payload.get("status") if isinstance(payload, Mapping) else None
            )
            if status == "Success":
                output = (
                    payload.get("outputConfiguration")
                    if isinstance(payload, Mapping)
                    else None
                )
                s3_uri = (
                    output.get("s3Uri") if isinstance(output, Mapping) else None
                )
                if not isinstance(s3_uri, str) or not s3_uri:
                    raise _malformed_output_failure(
                        self._provider,
                        "GetDataAutomationStatus success without outputConfiguration.s3Uri",
                    )
                return s3_uri
            if status == "ClientError":
                # A client-side rejection by BDA: not retryable, not billed.
                raise _rejected_failure(
                    self._provider,
                    self._status_message(payload, "Bedrock Data Automation client error"),
                )
            if status == "ServiceError":
                raise ProviderCallFailure(
                    provider=self._provider,
                    error_info=ProviderErrorInfo(
                        "provider_unavailable",
                        True,
                        None,
                        billable=False,
                        sent=True,
                    ),
                    message=self._status_message(
                        payload, "Bedrock Data Automation service error"
                    ),
                )
            # Created / InProgress / unknown -> keep polling until the deadline.
            if self._monotonic() >= deadline:
                raise _timeout_failure(
                    self._provider,
                    "Bedrock Data Automation job did not finish before the deadline",
                )
            self._sleep(interval)
            interval = min(interval * 1.5, self._settings.max_poll_interval_s)

    def _status_message(self, payload: object, default: str) -> str:
        if isinstance(payload, Mapping):
            message = payload.get("errorMessage") or payload.get("errorType")
            if isinstance(message, str) and message:
                return message
        return default

    # -- read standard output ----------------------------------------------
    def _read_standard_output(self, metadata_s3_uri: str) -> Mapping[str, object]:
        metadata = self._read_json_s3(metadata_s3_uri, _METADATA_MAX_BYTES)
        standard_output_uri = _find_standard_output_path(metadata)
        if standard_output_uri is None:
            raise _malformed_output_failure(
                self._provider,
                "job metadata has no standard_output_path",
            )
        result = self._read_json_s3(standard_output_uri, _RESULT_MAX_BYTES)
        if not isinstance(result, Mapping):
            raise _malformed_output_failure(
                self._provider, "standard output is not a JSON object"
            )
        return result

    def _read_json_s3(self, s3_uri: str, max_bytes: int) -> object:
        location = parse_s3_uri(s3_uri)
        data = self._store.read_object_bytes(location.key, max_bytes=max_bytes)
        try:
            return json.loads(data)
        except ValueError as exc:
            raise _malformed_output_failure(
                self._provider, f"could not parse JSON at {s3_uri}"
            ) from exc

    # -- cleanup ------------------------------------------------------------
    def _cleanup_objects(self, input_key: str, output_prefix: str) -> None:
        """Best-effort removal of staged objects; never changes the result."""
        try:
            self._store.remove_object(input_key)
            for key in self._store.list_object_keys(output_prefix):
                self._store.remove_object(key)
        except ProviderCallFailure:
            # Cleanup is best-effort: a failure here must not mask a good result.
            return

    # -- transport ----------------------------------------------------------
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

    def _post_json(self, target: str, body: Mapping[str, object]) -> object:
        payload_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        credentials = self._resolve_bedrock_credentials()
        amz_date = self._clock().strftime("%Y%m%dT%H%M%SZ")
        signed = sign_request(
            credentials=credentials,
            method="POST",
            host=self._settings.host,
            path="/",
            region=self._settings.region,
            service=BEDROCK_DATA_AUTOMATION_SERVICE,
            amz_date=amz_date,
            headers={
                "content-type": _JSON_CONTENT_TYPE,
                "x-amz-target": f"{_TARGET_PREFIX}.{target}",
            },
            payload=payload_bytes,
        )
        url = f"https://{self._settings.host}/"
        try:
            with build_client(
                timeout_s=self._settings.timeout_s, transport=self._transport
            ) as client:
                response = client.request(
                    "POST", url, headers=dict(signed.headers), content=payload_bytes
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(self._provider, exc) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(response.status_code, None),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise _malformed_output_failure(
                self._provider, f"{target} returned a non-JSON body"
            ) from exc


# ---------------------------------------------------------------------------
# Pure result mappers (BDA audio standard output -> neutral contracts).
# ---------------------------------------------------------------------------
def _audio_section(result: Mapping[str, object], key: str) -> Sequence[object]:
    """Read ``audio_items`` / ``audio_segments`` from the top level or ``audio``.

    BDA emits these at the top level; some blueprint shapes nest them under an
    ``audio`` key. Accept both.
    """
    direct = result.get(key)
    if isinstance(direct, Sequence) and not isinstance(direct, (str, bytes)):
        return direct
    audio = result.get("audio")
    if isinstance(audio, Mapping):
        nested = audio.get(key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            return nested
    return ()


def _result_metadata(result: Mapping[str, object]) -> Mapping[str, object]:
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata
    audio = result.get("audio")
    if isinstance(audio, Mapping):
        nested = audio.get("metadata")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _words_from_items(result: Mapping[str, object]) -> tuple[TranscriptWordOutput, ...]:
    words: list[TranscriptWordOutput] = []
    for item in _audio_section(result, "audio_items"):
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        start = item.get("start_timestamp_millis")
        end = item.get("end_timestamp_millis")
        if not isinstance(content, str) or not content:
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        start_ms = int(start)
        end_ms = int(end)
        if start_ms < 0 or end_ms < start_ms:
            # Skip invalid spans rather than fail the whole transcript.
            continue
        words.append(
            TranscriptWordOutput(text=content, start_ms=start_ms, end_ms=end_ms)
        )
    return tuple(words)


def _transcript_text(result: Mapping[str, object]) -> str:
    # Prefer joining the TRANSCRIPT segments; fall back to the representation text.
    parts: list[str] = []
    for segment in _audio_section(result, "audio_segments"):
        if not isinstance(segment, Mapping):
            continue
        if segment.get("type") not in (None, "TRANSCRIPT"):
            continue
        text = segment.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if parts:
        return " ".join(parts)
    transcript = result.get("transcript")
    if isinstance(transcript, Mapping):
        representation = transcript.get("representation")
        if isinstance(representation, Mapping):
            text = representation.get("text")
            if isinstance(text, str):
                return text
    return ""


def _dominant_language(result: Mapping[str, object]) -> str | None:
    language = _result_metadata(result).get("dominant_asset_language")
    if isinstance(language, str) and language:
        return language.lower()
    return None


def duration_seconds_from_result(result: Mapping[str, object]) -> float:
    """Audio duration in seconds from ``metadata.duration_millis`` (0.0 if absent)."""
    millis = _result_metadata(result).get("duration_millis")
    if isinstance(millis, (int, float)) and millis >= 0:
        return float(millis) / 1000.0
    # Fall back to the last segment/item end timestamp if duration is missing.
    last_end = 0
    for key in ("audio_segments", "audio_items"):
        for entry in _audio_section(result, key):
            if isinstance(entry, Mapping):
                end = entry.get("end_timestamp_millis")
                if isinstance(end, (int, float)) and int(end) > last_end:
                    last_end = int(end)
    return float(last_end) / 1000.0


def transcription_from_result(result: Mapping[str, object]) -> TranscriptionOutput:
    """Map BDA audio standard output onto a :class:`TranscriptionOutput`."""
    words = _words_from_items(result)
    text = _transcript_text(result)
    language = _dominant_language(result)
    return TranscriptionOutput(
        text=text,
        language=language,
        confidence=None,
        words=words,
        raw_response={"api": "data_automation"},
    )


def diarization_from_result(result: Mapping[str, object]) -> DiarizationOutput:
    """Map BDA audio standard output onto a :class:`DiarizationOutput`.

    Raises :class:`DataAutomationOutputError` when no segment carries a
    ``speaker.speaker_label`` — the operator must enable speaker labeling in the
    BDA project.
    """
    segments: list[DiarizationSegmentOutput] = []
    for segment in _audio_section(result, "audio_segments"):
        if not isinstance(segment, Mapping):
            continue
        speaker = segment.get("speaker")
        label = speaker.get("speaker_label") if isinstance(speaker, Mapping) else None
        start = segment.get("start_timestamp_millis")
        end = segment.get("end_timestamp_millis")
        if not isinstance(label, str) or not label.strip():
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        start_ms = int(start)
        end_ms = int(end)
        if start_ms < 0 or end_ms < start_ms:
            continue
        segments.append(
            DiarizationSegmentOutput(speaker=label, start_ms=start_ms, end_ms=end_ms)
        )
    if not segments:
        raise DataAutomationOutputError(
            "no speaker labels in the Bedrock Data Automation output; "
            "enable speaker labeling in the BDA project"
        )
    return DiarizationOutput(
        segments=sort_diarization_segments(segments),
        raw_response={"api": "data_automation"},
    )


def usage_input_units_from_result(result: Mapping[str, object]) -> int:
    """Billable input units for an audio job = ``ceil(duration_seconds)``."""
    return int(math.ceil(duration_seconds_from_result(result)))


def _find_standard_output_path(metadata: object) -> str | None:
    """Locate the first ``standard_output_path`` in the BDA ``job_metadata.json``.

    Shape (confirmed): ``output_metadata[].segment_metadata[].standard_output_path``.
    """
    if not isinstance(metadata, Mapping):
        return None
    output_metadata = metadata.get("output_metadata")
    if not isinstance(output_metadata, Sequence) or isinstance(
        output_metadata, (str, bytes)
    ):
        return None
    for entry in output_metadata:
        if not isinstance(entry, Mapping):
            continue
        segments = entry.get("segment_metadata")
        if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
            continue
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            path = segment.get("standard_output_path")
            if isinstance(path, str) and path.startswith("s3://"):
                return path
    return None
