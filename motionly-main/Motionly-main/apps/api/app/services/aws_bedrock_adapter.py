"""AWS Bedrock adapter with the IAM credential chain and SigV4 (plan Task 25).

A ``aws_bedrock`` registry adapter calls Amazon Bedrock without ``botocore`` and
without a static cloud key stored as an AI secret (plan Task 25 "the IAM path
stores no static cloud key as an AI secret"). Credentials come from the standard
IAM chain, cached with expiry and refreshed before they lapse, and every
resolved secret value is registered with the log redactor in
``app.core.logging`` so it can never be logged.

Two Bedrock protocols are mapped as adapter/config data (plan Task 25
"Converse and InvokeModel mappings are adapter or config data"):

* **Converse** (``POST /model/<id>/converse``) for ``planning.chat`` and image
  captions (``vision.caption``). The Converse API gives one neutral message
  shape across model families, so no per-family request table is needed.
* **InvokeModel** (``POST /model/<id>/invoke``) for ``embeddings.text``, whose
  request/response shapes differ per model family. Those shapes are a data table
  (:data:`EMBEDDING_MODEL_FAMILIES`), selected by the route's configured family.

The endpoint host is fixed by region (``bedrock-runtime.<region>.amazonaws.com``)
and never templated. "AWS"/"Bedrock" may be named here (not on the Task 2
scanner's audited vendor list); the Motionly core never names them.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.logging import register_secret_value
from app.services.aws_sigv4 import SigV4Credentials, sign_request
from app.services.inference_http import (
    build_client,
    exception_failure,
    malformed_failure,
    read_json_response,
)
from app.services.model_gateway import (
    AdapterResult,
    DetectedObjectOutput,
    DiarizationRequest,
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    PlanningOutput,
    PlanningRequest,
    TranscriptionRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

ADAPTER_TYPE = "aws_bedrock"
BEDROCK_SERVICE = "bedrock"

logger = logging.getLogger("app.services.aws_bedrock_adapter")

# IMDSv2 endpoint and the ECS credential provider host (link-local, non-routable).
_IMDS_HOST = "169.254.169.254"
_IMDS_TOKEN_PATH = "/latest/api/token"  # noqa: S105 - a public well-known path, not a secret
_IMDS_ROLE_PATH = "/latest/meta-data/iam/security-credentials/"
_ECS_CREDENTIAL_HOST = "169.254.170.2"
# Refresh this many seconds before expiry so an in-flight request never carries
# a just-lapsed credential.
_EXPIRY_MARGIN_S = 60.0
# A static IAM-user key mounted as a secret file (S1) is re-read from disk at
# most this often, so a rotated key is picked up without a restart while a
# healthy key costs one file read per interval, not per request.
_STATIC_KEY_REREAD_S = 300.0


class BedrockConfigError(ValueError):
    """An ``aws_bedrock`` adapter configuration is invalid (never a secret)."""


# ---------------------------------------------------------------------------
# Embedding model-family request/response shapes (InvokeModel), as a data table.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EmbeddingModelFamily:
    """One model family's InvokeModel embedding request/response shape.

    ``build_body`` turns a list of input strings into the family's request JSON;
    ``extract`` reads the vectors out of the response. Families that embed one
    input per call are marked ``single_input`` so the adapter loops.
    """

    name: str
    build_body: Callable[[Sequence[str]], Mapping[str, object]]
    extract: Callable[[Mapping[str, object]], list[list[float]]]
    single_input: bool = False


def _amazon_titan_body(inputs: Sequence[str]) -> Mapping[str, object]:
    # Titan embeds exactly one input text per request.
    return {"inputText": inputs[0]}


def _amazon_titan_extract(payload: Mapping[str, object]) -> list[list[float]]:
    vector = payload.get("embedding")
    if not isinstance(vector, Sequence):
        raise _ExtractError
    return [[float(value) for value in vector]]


def _cohere_body(inputs: Sequence[str]) -> Mapping[str, object]:
    return {"texts": list(inputs), "input_type": "search_document"}


def _cohere_extract(payload: Mapping[str, object]) -> list[list[float]]:
    rows = payload.get("embeddings")
    if not isinstance(rows, Sequence):
        raise _ExtractError
    return [[float(value) for value in row] for row in rows]


class _ExtractError(Exception):
    """A family extractor could not read vectors (mapped to malformed)."""


EMBEDDING_MODEL_FAMILIES: Mapping[str, EmbeddingModelFamily] = {
    "titan": EmbeddingModelFamily(
        name="titan",
        build_body=_amazon_titan_body,
        extract=_amazon_titan_extract,
        single_input=True,
    ),
    "cohere": EmbeddingModelFamily(
        name="cohere",
        build_body=_cohere_body,
        extract=_cohere_extract,
    ),
}


# ---------------------------------------------------------------------------
# IAM credential chain
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _CachedCredentials:
    credentials: SigV4Credentials
    expires_at: float | None  # monotonic seconds; None = static (never expires)


class CredentialResolutionError(RuntimeError):
    """No IAM credentials could be resolved from any source in the chain."""


class StaticKeyResolutionError(RuntimeError):
    """A static IAM-user key (S1) could not be read from its secret refs.

    Raised when the injected ``static_key_source`` cannot produce credentials
    (a missing, empty or unreadable secret file). The message names neither the
    key nor the secret value — only that the configured refs could not be read.
    """


class IamCredentialChain:
    """Resolve IAM credentials from env vars, ECS, then EC2 IMDSv2.

    Credentials are cached and refreshed before expiry. Every resolved secret
    value (access key, secret key, session token) is registered with the log
    redactor. A ``transport`` and ``env``/``clock`` are injected so tests drive
    each source with a fake.

    When a ``static_key_source`` is injected (S1, the ``aws_access_key`` auth
    path) it is used **exclusively**: the env/ECS/IMDS chain is never consulted,
    there is no fallback. The source is a callable resolving the secret refs to
    :class:`SigV4Credentials`; it is called **lazily** on the first signed
    request (never at runtime build time, so non-credentialed containers can
    still build the full runtime), its result is cached, and the source is
    re-read at most every :data:`_STATIC_KEY_REREAD_S` seconds so a rotated key
    is picked up without a restart. A failure raises
    :class:`StaticKeyResolutionError` and no request is sent.
    """

    def __init__(
        self,
        *,
        region: str,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        imds_timeout_s: float = 2.0,
        static_key_source: Callable[[], SigV4Credentials] | None = None,
    ) -> None:
        self._region = region
        self._env = dict(env)
        self._transport = transport
        self._clock = clock
        self._now = now
        self._imds_timeout_s = imds_timeout_s
        self._lock = threading.Lock()
        self._cached: _CachedCredentials | None = None
        # Static IAM-user key path (S1). ``_static_key_source`` is the lazy
        # resolver; ``_static_cached`` / ``_static_read_at`` hold the cached
        # credential and the monotonic time it was last read.
        self._static_key_source = static_key_source
        self._static_cached: SigV4Credentials | None = None
        self._static_read_at: float | None = None

    def resolve(self) -> SigV4Credentials:
        if self._static_key_source is not None:
            return self._resolve_static_key()
        cached = self._cached
        if cached is not None and not self._expired(cached):
            return cached.credentials
        with self._lock:
            cached = self._cached
            if cached is not None and not self._expired(cached):
                return cached.credentials
            resolved = self._resolve_fresh()
            self._register(resolved.credentials)
            self._cached = resolved
            return resolved.credentials

    def _resolve_static_key(self) -> SigV4Credentials:
        """Resolve the injected static-key source, cached and re-read lazily.

        Used exclusively when a ``static_key_source`` is set: never falls back to
        env/ECS/IMDS. The source (and therefore the secret files) is read at most
        once per :data:`_STATIC_KEY_REREAD_S`. Any failure is wrapped in
        :class:`StaticKeyResolutionError` so the caller sends no request.
        """
        source = self._static_key_source
        assert source is not None  # noqa: S101 - guarded by the caller
        now = self._clock()
        cached = self._static_cached
        read_at = self._static_read_at
        if (
            cached is not None
            and read_at is not None
            and now - read_at < _STATIC_KEY_REREAD_S
        ):
            return cached
        with self._lock:
            now = self._clock()
            if (
                self._static_cached is not None
                and self._static_read_at is not None
                and now - self._static_read_at < _STATIC_KEY_REREAD_S
            ):
                return self._static_cached
            try:
                credentials = source()
            except StaticKeyResolutionError:
                raise
            except Exception as exc:  # noqa: BLE001 - never leak the value/cause
                raise StaticKeyResolutionError(
                    "static aws_access_key refs could not be resolved"
                ) from exc
            # The source already registered the secret with the redactor, but
            # register the resolved credential again defensively.
            self._register(credentials)
            self._static_cached = credentials
            self._static_read_at = now
            return credentials

    def _expired(self, cached: _CachedCredentials) -> bool:
        if cached.expires_at is None:
            return False
        return self._clock() >= cached.expires_at - _EXPIRY_MARGIN_S

    def _resolve_fresh(self) -> _CachedCredentials:
        for source in (self._from_env, self._from_ecs, self._from_imds):
            resolved = source()
            if resolved is not None:
                return resolved
        raise CredentialResolutionError(
            "no IAM credentials found (env, ECS container, EC2 IMDSv2)"
        )

    def _register(self, credentials: SigV4Credentials) -> None:
        register_secret_value(credentials.secret_access_key)
        if credentials.session_token:
            register_secret_value(credentials.session_token)

    # -- sources ------------------------------------------------------------
    def _from_env(self) -> _CachedCredentials | None:
        access = self._env.get("AWS_ACCESS_KEY_ID")
        secret = self._env.get("AWS_SECRET_ACCESS_KEY")
        if not access or not secret:
            return None
        return _CachedCredentials(
            credentials=SigV4Credentials(
                access_key_id=access,
                secret_access_key=secret,
                session_token=self._env.get("AWS_SESSION_TOKEN") or None,
            ),
            expires_at=None,
        )

    def _from_ecs(self) -> _CachedCredentials | None:
        relative = self._env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        full = self._env.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        if relative:
            url = f"http://{_ECS_CREDENTIAL_HOST}{relative}"
        elif full:
            url = full
        else:
            return None
        payload = self._get_json(url, headers={})
        return self._parse_role_credentials(payload)

    def _from_imds(self) -> _CachedCredentials | None:
        # IMDSv2: fetch a session token, then the role name, then its creds.
        token = self._imds_token()
        if token is None:
            return None
        headers = {"x-aws-ec2-metadata-token": token}
        role = self._get_text(f"http://{_IMDS_HOST}{_IMDS_ROLE_PATH}", headers=headers)
        if not role:
            return None
        payload = self._get_json(
            f"http://{_IMDS_HOST}{_IMDS_ROLE_PATH}{role.strip()}", headers=headers
        )
        return self._parse_role_credentials(payload)

    def _imds_token(self) -> str | None:
        try:
            with self._client() as client:
                response = client.request(
                    "PUT",
                    f"http://{_IMDS_HOST}{_IMDS_TOKEN_PATH}",
                    headers={"x-aws-ec2-metadata-token-ttl-seconds": "21600"},
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if response.status_code != 200:
            return None
        token = response.text.strip()
        return token or None

    def _parse_role_credentials(
        self, payload: object
    ) -> _CachedCredentials | None:
        if not isinstance(payload, Mapping):
            return None
        access = payload.get("AccessKeyId")
        secret = payload.get("SecretAccessKey")
        token = payload.get("Token")
        if not isinstance(access, str) or not isinstance(secret, str):
            return None
        expires_at = self._expiry_seconds(payload.get("Expiration"))
        return _CachedCredentials(
            credentials=SigV4Credentials(
                access_key_id=access,
                secret_access_key=secret,
                session_token=token if isinstance(token, str) and token else None,
            ),
            expires_at=expires_at,
        )

    def _expiry_seconds(self, expiration: object) -> float | None:
        if not isinstance(expiration, str) or not expiration:
            return None
        try:
            when = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        except ValueError:
            return None
        delta = (when - self._now()).total_seconds()
        # Map the wall-clock lifetime onto the injected monotonic clock.
        return self._clock() + max(0.0, delta)

    # -- transport ----------------------------------------------------------
    def _client(self) -> httpx.Client:
        return build_client(timeout_s=self._imds_timeout_s, transport=self._transport)

    def _get_json(self, url: str, *, headers: Mapping[str, str]) -> object:
        text = self._get_text(url, headers=headers)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _get_text(self, url: str, *, headers: Mapping[str, str]) -> str | None:
        try:
            with self._client() as client:
                response = client.request("GET", url, headers=dict(headers))
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if response.status_code != 200:
            return None
        return response.text


# ---------------------------------------------------------------------------
# Config, validation and the adapter
# ---------------------------------------------------------------------------
# The four Bedrock protocols the adapter maps as config data. ``converse`` and
# ``invoke_model`` are the original two (planning/captions/detection and
# embeddings); ``data_automation`` and ``async_invoke`` are added for CPU-only
# transcription/diarization and text-to-video respectively (spec item E).
_BEDROCK_APIS = ("converse", "invoke_model", "data_automation", "async_invoke")


@dataclass(frozen=True)
class BedrockConfig:
    """Configuration for one ``aws_bedrock`` adapter record.

    ``region`` fixes the endpoint host. ``api`` is one of ``converse`` (planning,
    captions and object detection), ``invoke_model`` (embeddings),
    ``data_automation`` (transcription and diarization through Bedrock Data
    Automation) or ``async_invoke`` (text-to-video generation).
    ``embedding_family`` selects the InvokeModel request/response shape and is
    required/valid only for ``invoke_model``.

    The remaining optional fields are additive and apply only to the two new
    apis (they default to ``None`` / their documented defaults for
    ``converse`` / ``invoke_model`` so existing records and tests are
    unchanged):

    * ``data_automation`` requires an S3 staging area (``s3_bucket``,
      ``s3_prefix``, ``s3_region``) and ``data_automation_profile_arn``; a
      ``data_automation_project_arn`` is required for diarization (speaker
      labeling is enabled on the project).
    * ``async_invoke`` requires an output ``s3_bucket`` and a ``video_family``
      (``nova_reel`` / ``luma_ray``); ``video_resolution`` and
      ``presign_expires_s`` tune the output.
    """

    region: str
    api: str
    embedding_family: str | None = None
    timeout_s: float = 60.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    # S3 staging / output area shared by data_automation and async_invoke.
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_region: str | None = None
    # data_automation (transcription + diarization) fields.
    data_automation_profile_arn: str | None = None
    data_automation_project_arn: str | None = None
    data_automation_stage: str = "LIVE"
    max_wait_s: float = 900.0
    poll_interval_s: float = 5.0
    cleanup: bool = True
    # async_invoke (text-to-video) fields.
    video_family: str | None = None
    video_resolution: str = "720p"
    presign_expires_s: int = 900

    def __post_init__(self) -> None:
        errors = validate_bedrock_config(
            region=self.region,
            api=self.api,
            embedding_family=self.embedding_family,
            timeout_s=self.timeout_s,
            s3_bucket=self.s3_bucket,
            s3_region=self.s3_region,
            data_automation_profile_arn=self.data_automation_profile_arn,
            data_automation_project_arn=self.data_automation_project_arn,
            video_family=self.video_family,
        )
        if errors:
            raise BedrockConfigError("; ".join(errors))

    @property
    def host(self) -> str:
        return f"bedrock-runtime.{self.region}.amazonaws.com"

    @property
    def effective_s3_region(self) -> str:
        """The S3 region (``s3_region`` when set, else the Bedrock region)."""
        return self.s3_region or self.region


def validate_bedrock_config(
    *,
    region: str,
    api: str,
    embedding_family: str | None,
    timeout_s: float,
    s3_bucket: str | None = None,
    s3_region: str | None = None,
    data_automation_profile_arn: str | None = None,
    data_automation_project_arn: str | None = None,
    video_family: str | None = None,
) -> list[str]:
    """Validate an ``aws_bedrock`` config; return human-readable errors.

    Never resolves a credential and never echoes a secret (plan Task 25
    "validation"). The registry/runtime wave calls this before activating. The
    new keyword arguments are optional and default to ``None`` so every existing
    caller (``converse`` / ``invoke_model``) keeps working unchanged.
    """
    errors: list[str] = []
    if not region or "/" in region or " " in region:
        errors.append("region must be a non-empty AWS region id")
    if api not in _BEDROCK_APIS:
        errors.append(f"api must be one of {sorted(_BEDROCK_APIS)}")
    if api == "invoke_model":
        if embedding_family is None:
            errors.append("invoke_model requires embedding_family")
        elif embedding_family not in EMBEDDING_MODEL_FAMILIES:
            errors.append(
                f"embedding_family must be one of {sorted(EMBEDDING_MODEL_FAMILIES)}"
            )
    elif embedding_family is not None:
        errors.append("embedding_family is only valid for invoke_model")
    if api in ("data_automation", "async_invoke"):
        errors.extend(_validate_s3_bucket(s3_bucket, s3_region))
    if api == "data_automation":
        errors.extend(_validate_data_automation_fields(data_automation_profile_arn,
                                                        data_automation_project_arn))
    if api == "async_invoke":
        errors.extend(_validate_async_invoke_fields(video_family))
    if not (
        isinstance(timeout_s, (int, float))
        and not isinstance(timeout_s, bool)
        and timeout_s > 0
    ):
        errors.append("timeout_s must be a positive number")
    return errors


def _validate_s3_bucket(s3_bucket: str | None, s3_region: str | None) -> list[str]:
    """Validate the shared S3 staging/output bucket for the two new apis."""
    from app.services.aws_s3_objects import validate_bucket_name

    errors: list[str] = []
    if not s3_bucket:
        errors.append("s3_bucket is required for this api")
    else:
        for problem in validate_bucket_name(s3_bucket):
            errors.append(f"s3_bucket invalid: {problem}")
    if s3_region is not None and ("/" in s3_region or " " in s3_region):
        errors.append("s3_region must be a valid AWS region id")
    return errors


def _validate_data_automation_fields(
    profile_arn: str | None, project_arn: str | None
) -> list[str]:
    """Validate the data_automation profile/project ARNs via their module."""
    from app.services.aws_bedrock_data_automation import (
        validate_data_automation_arns,
    )

    if not profile_arn:
        return ["data_automation requires data_automation_profile_arn"]
    return validate_data_automation_arns(
        profile_arn=profile_arn, project_arn=project_arn
    )


def _validate_async_invoke_fields(video_family: str | None) -> list[str]:
    """Validate the async_invoke video family against the known families."""
    from app.services.aws_bedrock_async_invoke import VIDEO_MODEL_FAMILIES

    if not video_family:
        return ["async_invoke requires video_family"]
    if video_family not in VIDEO_MODEL_FAMILIES:
        return [f"video_family must be one of {sorted(VIDEO_MODEL_FAMILIES)}"]
    return []


class AwsBedrockAdapter:
    """A typed, config-driven Amazon Bedrock adapter using IAM + SigV4."""

    def __init__(
        self,
        *,
        config: BedrockConfig,
        credentials: IamCredentialChain,
        provider: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        data_automation_runner: object | None = None,
        video_runner: object | None = None,
    ) -> None:
        self._config = config
        self._credentials = credentials
        self._provider = provider
        self._transport = transport
        self._clock = clock
        # ``data_automation`` and ``async_invoke`` delegate to a runner built by
        # the factory (it owns the shared S3 store and credential chain). They
        # are ``None`` for ``converse`` / ``invoke_model``.
        self._data_automation_runner = data_automation_runner
        self._video_runner = video_runner

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        provider = self._provider or route.provider
        if self._config.api == "converse":
            if isinstance(request, PlanningRequest):
                return self._converse_planning(provider, route, request)
            if isinstance(request, ObjectDetectionRequest):
                return self._converse_object_detection(provider, route, request)
            if isinstance(request, VisionRequest):
                return self._converse_caption(provider, route, request)
            raise TypeError(
                "converse serves PlanningRequest, VisionRequest and "
                "ObjectDetectionRequest"
            )
        if self._config.api == "invoke_model":
            if isinstance(request, EmbeddingsRequest):
                return self._invoke_embeddings(provider, route, request)
            raise TypeError("invoke_model serves EmbeddingsRequest (embeddings.text)")
        if self._config.api == "data_automation":
            if isinstance(request, TranscriptionRequest):
                return self._data_automation_transcription(provider, request)
            if isinstance(request, DiarizationRequest):
                return self._data_automation_diarization(provider, request)
            raise TypeError(
                "data_automation serves TranscriptionRequest and DiarizationRequest"
            )
        if self._config.api == "async_invoke":
            if isinstance(request, VideoGenerationSubmitRequest):
                return self._async_invoke_submit(provider, route, request)
            if isinstance(request, VideoGenerationPollRequest):
                return self._async_invoke_poll(provider, request)
            raise TypeError(
                "async_invoke serves VideoGenerationSubmitRequest and "
                "VideoGenerationPollRequest"
            )
        raise TypeError(f"unsupported Bedrock api {self._config.api!r}")

    # -- Converse -----------------------------------------------------------
    def _converse_planning(
        self, provider: str, route: ModelRoute, request: PlanningRequest
    ) -> AdapterResult:
        body = _converse_body(
            messages=[
                {"role": _role(m.role), "content": [{"text": m.content}]}
                for m in request.messages
            ],
            system=[m.content for m in request.messages if m.role == "system"],
            max_tokens=request.max_output_units,
            temperature=request.temperature,
        )
        payload = self._send_model(provider, route.model, "converse", body)
        content = _converse_text(payload)
        if not content:
            raise malformed_failure(provider)
        return AdapterResult(
            output=PlanningOutput(content=content, raw_response={"api": "converse"}),
            usage=_converse_usage(payload),
            usage_reported=True,
        )

    def _converse_caption(
        self, provider: str, route: ModelRoute, request: VisionRequest
    ) -> AdapterResult:
        # Captions send the prompt plus image references as Converse content. A
        # ``data:`` URL is converted to an inline bytes block (Converse cannot
        # fetch a data URL by reference); every other URL keeps its existing
        # by-reference behavior.
        from app.services.aws_bedrock_vision import image_block_from_data_url

        content_blocks: list[Mapping[str, object]] = [{"text": request.prompt}]
        for url in request.image_urls:
            try:
                data_block = image_block_from_data_url(url)
            except ValueError as exc:
                raise malformed_failure(provider) from exc
            if data_block is not None:
                content_blocks.append(data_block)
            else:
                content_blocks.append(
                    {"image": {"format": "jpeg", "source": {"url": url}}}
                )
        body = _converse_body(
            messages=[{"role": "user", "content": content_blocks}],
            system=[],
            max_tokens=request.max_output_units,
            temperature=0.2,
        )
        payload = self._send_model(provider, route.model, "converse", body)
        text = _converse_text(payload)
        if not text:
            raise malformed_failure(provider)
        return AdapterResult(
            output=VisionOutput(content=text, raw_response={"api": "converse"}),
            usage=_converse_usage(payload),
            usage_reported=True,
        )

    def _converse_object_detection(
        self, provider: str, route: ModelRoute, request: ObjectDetectionRequest
    ) -> AdapterResult:
        """Object detection through one Converse call per image (spec item E).

        Each image is sent as its own Converse request with the Motionly-owned
        detection prompt. A per-image answer that cannot be parsed (a
        ``DetectionParseError``) yields an empty tuple for that image plus a
        warning; a well-formed empty array is a valid "nothing detected" result.
        If *every* image is unparsable the whole call fails as ``malformed``.
        HTTP / transport failures propagate (they are route-affine provider
        failures). Usage sums the Converse token usage across the images.
        """
        from app.services.aws_bedrock_vision import (
            DetectionParseError,
            detection_converse_body,
            parse_detection_text,
        )

        per_image: list[tuple[DetectedObjectOutput, ...]] = []
        input_tokens = 0
        output_tokens = 0
        parsed_any = False
        for index, image in enumerate(request.images):
            body = detection_converse_body(image, max_results=request.max_results)
            payload = self._send_model(provider, route.model, "converse", body)
            usage = _converse_usage(payload)
            input_tokens += usage.input_units
            output_tokens += usage.output_units
            text = _converse_text(payload)
            try:
                detections = parse_detection_text(
                    text, max_results=request.max_results
                )
                parsed_any = True
            except DetectionParseError:
                logger.warning(
                    "aws_bedrock_object_detection_unparsable",
                    extra={"provider": provider, "image_index": index},
                )
                detections = ()
            per_image.append(detections)
        if request.images and not parsed_any:
            raise malformed_failure(provider)
        return AdapterResult(
            output=ObjectDetectionOutput(
                detections=tuple(per_image),
                raw_response={"api": "converse", "images": len(request.images)},
            ),
            usage=ModelUsage(input_units=input_tokens, output_units=output_tokens),
            usage_reported=True,
        )

    # -- Data Automation (transcription + diarization) ---------------------
    def _data_automation_transcription(
        self, provider: str, request: TranscriptionRequest
    ) -> AdapterResult:
        from app.services.aws_bedrock_data_automation import (
            transcription_from_result,
            usage_input_units_from_result,
        )

        runner = self._require_data_automation_runner()
        result = runner.run_audio_analysis(
            request.audio_path, idempotency_scope="transcription"
        )
        output = transcription_from_result(result)
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=usage_input_units_from_result(result)),
            usage_reported=True,
        )

    def _data_automation_diarization(
        self, provider: str, request: DiarizationRequest
    ) -> AdapterResult:
        from app.services.aws_bedrock_data_automation import (
            DataAutomationOutputError,
            diarization_from_result,
            usage_input_units_from_result,
        )

        runner = self._require_data_automation_runner()
        result = runner.run_audio_analysis(
            request.audio_path, idempotency_scope="diarization"
        )
        try:
            output = diarization_from_result(result)
        except DataAutomationOutputError as exc:
            raise malformed_failure(provider) from exc
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=usage_input_units_from_result(result)),
            usage_reported=True,
        )

    def _require_data_automation_runner(self) -> object:
        if self._data_automation_runner is None:
            raise TypeError("data_automation api was built without a runner")
        return self._data_automation_runner

    # -- async-invoke (text-to-video) --------------------------------------
    def _async_invoke_submit(
        self, provider: str, route: ModelRoute, request: VideoGenerationSubmitRequest
    ) -> AdapterResult:
        from app.services.model_gateway import GenerationOperationOutput

        runner = self._require_video_runner()
        output = runner.start_video_job(route.model, request)
        if not isinstance(output, GenerationOperationOutput):
            raise malformed_failure(provider)
        return AdapterResult(output=output, usage=ModelUsage(), usage_reported=True)

    def _async_invoke_poll(
        self, provider: str, request: VideoGenerationPollRequest
    ) -> AdapterResult:
        runner = self._require_video_runner()
        output = runner.check_video_job(request.operation_id)
        return AdapterResult(output=output, usage=ModelUsage(), usage_reported=True)

    def _require_video_runner(self) -> object:
        if self._video_runner is None:
            raise TypeError("async_invoke api was built without a runner")
        return self._video_runner

    # -- InvokeModel (embeddings) ------------------------------------------
    def _invoke_embeddings(
        self, provider: str, route: ModelRoute, request: EmbeddingsRequest
    ) -> AdapterResult:
        family = EMBEDDING_MODEL_FAMILIES[self._config.embedding_family or ""]
        vectors: list[list[float]] = []
        inputs = list(request.inputs)
        batches = [[text] for text in inputs] if family.single_input else [inputs]
        for batch in batches:
            body = family.build_body(batch)
            payload = self._send_model(provider, route.model, "invoke", body)
            if not isinstance(payload, Mapping):
                raise malformed_failure(provider)
            try:
                vectors.extend(family.extract(payload))
            except (_ExtractError, TypeError, ValueError) as exc:
                raise malformed_failure(provider) from exc
        if len(vectors) != len(inputs):
            raise malformed_failure(provider)
        try:
            output = EmbeddingsOutput(
                vectors=tuple(tuple(v) for v in vectors),
                raw_response={"count": len(vectors)},
            )
        except ValueError as exc:
            raise malformed_failure(provider) from exc
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=len(vectors)),
            usage_reported=True,
        )

    # -- transport ----------------------------------------------------------
    def _send_model(
        self, provider: str, model: str, operation: str, body: Mapping[str, object]
    ) -> object:
        payload_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        path = f"/model/{model}/{operation}"
        amz_date = self._clock().strftime("%Y%m%dT%H%M%SZ")
        try:
            credentials = self._credentials.resolve()
        except StaticKeyResolutionError as exc:
            # A static IAM-user key (S1) that cannot be read is a build-time/ops
            # problem, not a provider outage: the request is never sent, so it is
            # non-billable, not circuit-worthy, and a different route may still
            # serve the request (fallback eligible). The message never carries
            # the secret value.
            raise ProviderCallFailure(
                provider=provider,
                error_info=ProviderErrorInfo(
                    "provider_auth_failed",
                    False,
                    fallback_eligible=True,
                    circuit_worthy=False,
                    billable=False,
                    sent=False,
                ),
            ) from exc
        except CredentialResolutionError as exc:
            raise ProviderCallFailure(
                provider=provider,
                error_info=ProviderErrorInfo(
                    "provider_auth_failed",
                    False,
                    fallback_eligible=True,
                    circuit_worthy=True,
                ),
            ) from exc
        signed = sign_request(
            credentials=credentials,
            method="POST",
            host=self._config.host,
            path=path,
            region=self._config.region,
            service=BEDROCK_SERVICE,
            amz_date=amz_date,
            headers={**self._config.extra_headers, "content-type": "application/json"},
            payload=payload_bytes,
        )
        url = f"https://{self._config.host}{path}"
        try:
            with build_client(
                timeout_s=self._config.timeout_s, transport=self._transport
            ) as client:
                response = client.request(
                    "POST", url, headers=dict(signed.headers), content=payload_bytes
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(provider, exc) from None
        return read_json_response(response, provider=provider)


def aws_bedrock_factory(
    *,
    config: BedrockConfig,
    env: Mapping[str, str],
    provider: str | None = None,
    credential_transport: httpx.BaseTransport | None = None,
    bedrock_transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    static_key_source: Callable[[], SigV4Credentials] | None = None,
) -> AwsBedrockAdapter:
    """Build an :class:`AwsBedrockAdapter` with an IAM credential chain.

    The registry/runtime wave calls this factory. ``env`` is the process
    environment the chain reads for env-var and ECS/IMDS discovery; the
    transports are injection points for tests. For ``data_automation`` /
    ``async_invoke`` the factory also builds the shared S3 object store and the
    matching runner, all sharing the one :class:`IamCredentialChain` (plan item
    E).

    When ``static_key_source`` is given (S1, the ``aws_access_key`` auth path)
    the chain uses it **exclusively** and never reads ``env``/ECS/IMDS. The
    source is resolved lazily on the first signed request (never here at build
    time), so a non-credentialed container can still build the full runtime
    without dropping any Bedrock route.
    """
    chain = IamCredentialChain(
        region=config.region,
        env=env,
        transport=credential_transport,
        static_key_source=static_key_source,
    )
    provider_label = provider or ADAPTER_TYPE
    data_automation_runner = None
    video_runner = None
    if config.api == "data_automation":
        data_automation_runner = _build_data_automation_runner(
            config, chain, provider_label, bedrock_transport, clock
        )
    elif config.api == "async_invoke":
        video_runner = _build_video_runner(
            config, chain, provider_label, bedrock_transport, clock
        )
    return AwsBedrockAdapter(
        config=config,
        credentials=chain,
        provider=provider,
        transport=bedrock_transport,
        clock=clock,
        data_automation_runner=data_automation_runner,
        video_runner=video_runner,
    )


def build_static_key_source(
    resolver: object,
    *,
    access_key_id_ref: str,
    secret_ref: str,
) -> Callable[[], SigV4Credentials]:
    """Build the lazy static-key source for the ``aws_access_key`` auth path (S1).

    ``resolver`` is the runtime :class:`SecretResolver` (duck-typed: it only
    needs a ``resolve(ref) -> str`` method that registers the value with the log
    redactor). The returned callable resolves the access-key-id and secret-key
    refs to :class:`SigV4Credentials` **each time it is invoked** — the
    :class:`IamCredentialChain` decides when to call it (lazily, then at most
    every :data:`_STATIC_KEY_REREAD_S` seconds). A missing/empty/unreadable ref
    raises :class:`StaticKeyResolutionError`; the message names neither the key
    nor the value.
    """
    resolve = resolver.resolve  # type: ignore[attr-defined]

    def _resolve_static_aws_access_key() -> SigV4Credentials:
        try:
            access_key_id = resolve(access_key_id_ref)
            secret_access_key = resolve(secret_ref)
        except Exception as exc:  # noqa: BLE001 - never leak the value/cause detail
            raise StaticKeyResolutionError(
                "static aws_access_key refs could not be resolved"
            ) from exc
        if not access_key_id or not secret_access_key:
            raise StaticKeyResolutionError(
                "static aws_access_key refs resolved to an empty value"
            )
        return SigV4Credentials(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=None,
        )

    return _resolve_static_aws_access_key


def _build_s3_store(
    config: BedrockConfig,
    chain: IamCredentialChain,
    provider_label: str,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
):
    """Build the shared :class:`S3ObjectStore` for the staging/output bucket."""
    from app.services.aws_s3_objects import S3ObjectStore

    return S3ObjectStore(
        region=config.effective_s3_region,
        bucket=config.s3_bucket or "",
        credentials=chain,
        provider=provider_label,
        transport=transport,
        clock=clock,
        timeout_s=config.timeout_s,
    )


def _build_data_automation_runner(
    config: BedrockConfig,
    chain: IamCredentialChain,
    provider_label: str,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
):
    from app.services.aws_bedrock_data_automation import (
        DataAutomationAudioRunner,
        DataAutomationSettings,
    )

    store = _build_s3_store(config, chain, provider_label, transport, clock)
    settings = DataAutomationSettings(
        region=config.region,
        profile_arn=config.data_automation_profile_arn or "",
        s3_bucket=config.s3_bucket or "",
        s3_prefix=config.s3_prefix,
        s3_region=config.effective_s3_region,
        project_arn=config.data_automation_project_arn,
        project_stage=config.data_automation_stage,
        max_wait_s=config.max_wait_s,
        poll_interval_s=config.poll_interval_s,
        cleanup=config.cleanup,
        timeout_s=config.timeout_s,
    )
    return DataAutomationAudioRunner(
        settings,
        credentials=chain,
        provider=provider_label,
        store=store,
        transport=transport,
        clock=clock,
    )


def _build_video_runner(
    config: BedrockConfig,
    chain: IamCredentialChain,
    provider_label: str,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
):
    from app.services.aws_bedrock_async_invoke import AsyncInvokeVideoRunner

    store = _build_s3_store(config, chain, provider_label, transport, clock)
    return AsyncInvokeVideoRunner(
        region=config.region,
        family=config.video_family or "",
        store=store,
        output_prefix=config.s3_prefix,
        provider=provider_label,
        credentials=chain,
        presign_expires_s=config.presign_expires_s,
        video_resolution=config.video_resolution,
        transport=transport,
        clock=clock,
        timeout_s=config.timeout_s,
    )


# ---------------------------------------------------------------------------
# Converse request/response helpers (data, not grammar).
# ---------------------------------------------------------------------------
def _role(role: str) -> str:
    # Converse accepts user/assistant turns; system prompts go in the system block.
    return "assistant" if role == "assistant" else "user"


def _converse_body(
    *,
    messages: Sequence[Mapping[str, object]],
    system: Sequence[str],
    max_tokens: int,
    temperature: float,
) -> Mapping[str, object]:
    turns = [m for m in messages if m.get("role") != "system"]
    body: dict[str, object] = {
        "messages": turns,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    system_blocks = [{"text": text} for text in system if text]
    if system_blocks:
        body["system"] = system_blocks
    return body


def _converse_text(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    content = (
        payload.get("output", {}).get("message", {}).get("content")
        if isinstance(payload.get("output"), Mapping)
        else None
    )
    if not isinstance(content, Sequence):
        return None
    parts = [block.get("text") for block in content if isinstance(block, Mapping)]
    text = "".join(part for part in parts if isinstance(part, str))
    return text or None


def _converse_usage(payload: object) -> ModelUsage:
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    if not isinstance(usage, Mapping):
        return ModelUsage()
    try:
        return ModelUsage(
            input_units=int(usage.get("inputTokens", 0)),
            output_units=int(usage.get("outputTokens", 0)),
        )
    except (TypeError, ValueError):
        return ModelUsage()
