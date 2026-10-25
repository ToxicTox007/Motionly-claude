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
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    PlanningRequest,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

ADAPTER_TYPE = "aws_bedrock"
BEDROCK_SERVICE = "bedrock"

# IMDSv2 endpoint and the ECS credential provider host (link-local, non-routable).
_IMDS_HOST = "169.254.169.254"
_IMDS_TOKEN_PATH = "/latest/api/token"  # noqa: S105 - a public well-known path, not a secret
_IMDS_ROLE_PATH = "/latest/meta-data/iam/security-credentials/"
_ECS_CREDENTIAL_HOST = "169.254.170.2"
# Refresh this many seconds before expiry so an in-flight request never carries
# a just-lapsed credential.
_EXPIRY_MARGIN_S = 60.0


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


class IamCredentialChain:
    """Resolve IAM credentials from env vars, ECS, then EC2 IMDSv2.

    Credentials are cached and refreshed before expiry. Every resolved secret
    value (access key, secret key, session token) is registered with the log
    redactor. A ``transport`` and ``env``/``clock`` are injected so tests drive
    each source with a fake.
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
    ) -> None:
        self._region = region
        self._env = dict(env)
        self._transport = transport
        self._clock = clock
        self._now = now
        self._imds_timeout_s = imds_timeout_s
        self._lock = threading.Lock()
        self._cached: _CachedCredentials | None = None

    def resolve(self) -> SigV4Credentials:
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
@dataclass(frozen=True)
class BedrockConfig:
    """Configuration for one ``aws_bedrock`` adapter record.

    ``region`` fixes the endpoint host. ``api`` is ``converse`` (planning and
    captions) or ``invoke_model`` (embeddings). ``embedding_family`` selects the
    InvokeModel request/response shape and is required/valid only for
    ``invoke_model``.
    """

    region: str
    api: str
    embedding_family: str | None = None
    timeout_s: float = 60.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        errors = validate_bedrock_config(
            region=self.region,
            api=self.api,
            embedding_family=self.embedding_family,
            timeout_s=self.timeout_s,
        )
        if errors:
            raise BedrockConfigError("; ".join(errors))

    @property
    def host(self) -> str:
        return f"bedrock-runtime.{self.region}.amazonaws.com"


def validate_bedrock_config(
    *,
    region: str,
    api: str,
    embedding_family: str | None,
    timeout_s: float,
) -> list[str]:
    """Validate an ``aws_bedrock`` config; return human-readable errors.

    Never resolves a credential and never echoes a secret (plan Task 25
    "validation"). The registry/runtime wave calls this before activating.
    """
    errors: list[str] = []
    if not region or "/" in region or " " in region:
        errors.append("region must be a non-empty AWS region id")
    if api not in ("converse", "invoke_model"):
        errors.append("api must be 'converse' or 'invoke_model'")
    if api == "invoke_model":
        if embedding_family is None:
            errors.append("invoke_model requires embedding_family")
        elif embedding_family not in EMBEDDING_MODEL_FAMILIES:
            errors.append(
                f"embedding_family must be one of {sorted(EMBEDDING_MODEL_FAMILIES)}"
            )
    elif embedding_family is not None:
        errors.append("embedding_family is only valid for invoke_model")
    if not (
        isinstance(timeout_s, (int, float))
        and not isinstance(timeout_s, bool)
        and timeout_s > 0
    ):
        errors.append("timeout_s must be a positive number")
    return errors


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
    ) -> None:
        self._config = config
        self._credentials = credentials
        self._provider = provider
        self._transport = transport
        self._clock = clock

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        provider = self._provider or route.provider
        if self._config.api == "converse":
            if isinstance(request, PlanningRequest):
                return self._converse_planning(provider, route, request)
            if isinstance(request, VisionRequest):
                return self._converse_caption(provider, route, request)
            raise TypeError("converse serves PlanningRequest and VisionRequest")
        if isinstance(request, EmbeddingsRequest):
            return self._invoke_embeddings(provider, route, request)
        raise TypeError("invoke_model serves EmbeddingsRequest (embeddings.text)")

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
        # Captions send the prompt plus image references as Converse content.
        content_blocks: list[Mapping[str, object]] = [{"text": request.prompt}]
        for url in request.image_urls:
            content_blocks.append({"image": {"format": "jpeg", "source": {"url": url}}})
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
) -> AwsBedrockAdapter:
    """Build an :class:`AwsBedrockAdapter` with an IAM credential chain.

    The registry/runtime wave calls this factory; it never passes a static AI
    secret (plan Task 25). ``env`` is the process environment the chain reads for
    env-var and ECS/IMDS discovery; the transports are injection points for tests.
    """
    chain = IamCredentialChain(
        region=config.region,
        env=env,
        transport=credential_transport,
    )
    return AwsBedrockAdapter(
        config=config,
        credentials=chain,
        provider=provider,
        transport=bedrock_transport,
        clock=clock,
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
