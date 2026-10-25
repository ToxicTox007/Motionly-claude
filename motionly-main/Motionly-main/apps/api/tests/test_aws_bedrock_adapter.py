"""Tests for the AWS Bedrock adapter and IAM credential chain (plan Task 25).

Fake transports drive the credential sources (env vars, ECS container endpoint,
EC2 IMDSv2) and the Bedrock Converse/InvokeModel APIs. Credential refresh, auth
failure, redactor registration and typed schema validation are checked. No real
secrets appear — only AWS's public example credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: F401 - kept for symmetry with other adapter tests

import httpx
import pytest

from app.core.logging import register_secret_value, registered_secret_values
from app.services.aws_bedrock_adapter import (
    EMBEDDING_MODEL_FAMILIES,
    AwsBedrockAdapter,
    BedrockConfig,
    BedrockConfigError,
    CredentialResolutionError,
    IamCredentialChain,
    aws_bedrock_factory,
    validate_bedrock_config,
)
from app.services.model_gateway import (
    ChatMessage,
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelCapability,
    ModelRoute,
    PlanningOutput,
    PlanningRequest,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import ProviderCallFailure


def AMZ_CLOCK() -> datetime:
    return datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)


def _route(capability: ModelCapability = ModelCapability.PLANNING) -> ModelRoute:
    return ModelRoute(
        route_id="r1",
        capability=capability,
        adapter="bedrock-1",
        provider="bedrock",
        model="test.model-v1",
        model_revision="2024-01-01",
    )


# --- validation ------------------------------------------------------------
def test_validate_converse_rejects_embedding_family() -> None:
    errors = validate_bedrock_config(
        region="us-east-1", api="converse", embedding_family="titan", timeout_s=1
    )
    assert any("only valid for invoke_model" in e for e in errors)


def test_validate_invoke_model_requires_family() -> None:
    errors = validate_bedrock_config(
        region="us-east-1", api="invoke_model", embedding_family=None, timeout_s=1
    )
    assert any("embedding_family" in e for e in errors)


def test_config_host_fixed_by_region() -> None:
    config = BedrockConfig(region="eu-west-2", api="converse")
    assert config.host == "bedrock-runtime.eu-west-2.amazonaws.com"


def test_config_raises_on_invalid() -> None:
    with pytest.raises(BedrockConfigError):
        BedrockConfig(region="us-east-1", api="bogus")


# --- credential chain ------------------------------------------------------
def test_env_credentials_resolved_and_registered() -> None:
    chain = IamCredentialChain(
        region="us-east-1",
        env={
            "AWS_ACCESS_KEY_ID": "AKIDEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI-secret-value-123456",
            "AWS_SESSION_TOKEN": "session-token-abcdef-123456",
        },
    )
    creds = chain.resolve()
    assert creds.access_key_id == "AKIDEXAMPLE"
    assert creds.session_token == "session-token-abcdef-123456"
    registered = registered_secret_values()
    assert "wJalrXUtnFEMI-secret-value-123456" in registered
    assert "session-token-abcdef-123456" in registered


def test_ecs_container_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "169.254.170.2"
        assert request.url.path == "/creds"
        return httpx.Response(
            200,
            json={
                "AccessKeyId": "ASIAEXAMPLE",
                "SecretAccessKey": "ecs-secret-value-abcdef",
                "Token": "ecs-token-value-abcdef",
                "Expiration": "2099-01-01T00:00:00Z",
            },
        )

    chain = IamCredentialChain(
        region="us-east-1",
        env={"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/creds"},
        transport=httpx.MockTransport(handler),
    )
    creds = chain.resolve()
    assert creds.access_key_id == "ASIAEXAMPLE"
    assert creds.session_token == "ecs-token-value-abcdef"


def test_imdsv2_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "169.254.169.254"
        if request.method == "PUT" and request.url.path == "/latest/api/token":
            assert request.headers["x-aws-ec2-metadata-token-ttl-seconds"] == "21600"
            return httpx.Response(200, text="imds-session-token")
        assert request.headers["x-aws-ec2-metadata-token"] == "imds-session-token"
        if request.url.path.endswith("/security-credentials/"):
            return httpx.Response(200, text="my-role")
        if request.url.path.endswith("/my-role"):
            return httpx.Response(
                200,
                json={
                    "AccessKeyId": "ASIAIMDS",
                    "SecretAccessKey": "imds-secret-value-abcdef",
                    "Token": "imds-token-value-abcdef",
                    "Expiration": "2099-01-01T00:00:00Z",
                },
            )
        raise AssertionError(f"unexpected IMDS path {request.url.path}")

    chain = IamCredentialChain(
        region="us-east-1", env={}, transport=httpx.MockTransport(handler)
    )
    creds = chain.resolve()
    assert creds.access_key_id == "ASIAIMDS"


def test_no_credentials_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    chain = IamCredentialChain(
        region="us-east-1", env={}, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CredentialResolutionError):
        chain.resolve()


def test_credentials_cached_and_refreshed_on_expiry() -> None:
    calls = {"count": 0}
    base = datetime(2024, 1, 1, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, text="t")
        if request.url.path.endswith("/security-credentials/"):
            return httpx.Response(200, text="role")
        calls["count"] += 1
        expires = (base + timedelta(seconds=120 * calls["count"])).isoformat().replace(
            "+00:00", "Z"
        )
        return httpx.Response(
            200,
            json={
                "AccessKeyId": f"ASIA{calls['count']}",
                "SecretAccessKey": f"secret-value-{calls['count']}-abcdef",
                "Token": "tok-abcdef-123456",
                "Expiration": expires,
            },
        )

    monotonic = {"t": 0.0}
    chain = IamCredentialChain(
        region="us-east-1",
        env={},
        transport=httpx.MockTransport(handler),
        clock=lambda: monotonic["t"],
        now=lambda: base,
    )
    first = chain.resolve()
    assert first.access_key_id == "ASIA1"
    # Within the cached window (minus the margin), no refresh.
    monotonic["t"] = 30.0
    assert chain.resolve().access_key_id == "ASIA1"
    # Past expiry minus margin, it refreshes.
    monotonic["t"] = 200.0
    assert chain.resolve().access_key_id == "ASIA2"


# --- Converse --------------------------------------------------------------
def _converse_adapter(handler, *, env=None):
    return aws_bedrock_factory(
        config=BedrockConfig(region="us-east-1", api="converse"),
        env=env or {"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "s" * 20},
        provider="bedrock",
        bedrock_transport=httpx.MockTransport(handler),
        clock=AMZ_CLOCK,
    )


def test_converse_planning_signs_request() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["path"] = request.url.path
        assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
        return httpx.Response(
            200,
            json={
                "output": {"message": {"content": [{"text": "a plan"}]}},
                "usage": {"inputTokens": 5, "outputTokens": 2},
            },
        )

    adapter = _converse_adapter(handler)
    result = adapter.invoke(
        route=_route(),
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )
    assert isinstance(result.output, PlanningOutput)
    assert result.output.content == "a plan"
    assert result.usage.input_units == 5
    assert seen["auth"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert seen["path"] == "/model/test.model-v1/converse"


def test_converse_caption() -> None:
    adapter = _converse_adapter(
        lambda r: httpx.Response(
            200, json={"output": {"message": {"content": [{"text": "a dog"}]}}}
        )
    )
    result = adapter.invoke(
        route=_route(),
        request=VisionRequest(prompt="caption", image_urls=("https://img.invalid/1.jpg",)),
    )
    assert isinstance(result.output, VisionOutput)
    assert result.output.content == "a dog"


def test_converse_malformed_response() -> None:
    adapter = _converse_adapter(lambda r: httpx.Response(200, json={"output": {}}))
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route(),
            request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
        )
    assert exc.value.error_info.code == "provider_malformed_response"


def test_auth_failure_when_no_credentials() -> None:
    adapter = aws_bedrock_factory(
        config=BedrockConfig(region="us-east-1", api="converse"),
        env={},
        provider="bedrock",
        credential_transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        bedrock_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        clock=AMZ_CLOCK,
    )
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route(),
            request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
        )
    assert exc.value.error_info.code == "provider_auth_failed"


# --- InvokeModel (embeddings) ---------------------------------------------
def _invoke_adapter(family, handler):
    return aws_bedrock_factory(
        config=BedrockConfig(region="us-east-1", api="invoke_model", embedding_family=family),
        env={"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "s" * 20},
        provider="bedrock",
        bedrock_transport=httpx.MockTransport(handler),
        clock=AMZ_CLOCK,
    )


def test_invoke_model_titan_single_input_loop() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    adapter = _invoke_adapter("titan", handler)
    result = adapter.invoke(
        route=_route(ModelCapability.EMBEDDINGS),
        request=EmbeddingsRequest(inputs=("a", "b")),
    )
    assert isinstance(result.output, EmbeddingsOutput)
    assert len(result.output.vectors) == 2  # one call per input
    assert len(bodies) == 2


def test_invoke_model_cohere_batched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    adapter = _invoke_adapter("cohere", handler)
    result = adapter.invoke(
        route=_route(ModelCapability.EMBEDDINGS),
        request=EmbeddingsRequest(inputs=("a", "b")),
    )
    assert isinstance(result.output, EmbeddingsOutput)
    assert result.output.vectors == ((0.1, 0.2), (0.3, 0.4))


def test_invoke_model_family_table_is_data() -> None:
    assert set(EMBEDDING_MODEL_FAMILIES) == {"titan", "cohere"}
    assert EMBEDDING_MODEL_FAMILIES["titan"].single_input is True


def test_direct_adapter_construction() -> None:
    # The adapter can also be built directly with a chain (no factory).
    chain = IamCredentialChain(
        region="us-east-1",
        env={"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "s" * 20},
    )
    adapter = AwsBedrockAdapter(
        config=BedrockConfig(region="us-east-1", api="converse"),
        credentials=chain,
        provider="bedrock",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"output": {"message": {"content": [{"text": "ok"}]}}}
            )
        ),
        clock=AMZ_CLOCK,
    )
    result = adapter.invoke(
        route=_route(),
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )
    assert result.output.content == "ok"


def test_register_secret_value_is_the_hook() -> None:
    # Document that the credential chain uses the app.core.logging redactor hook.
    register_secret_value("bedrock-probe-value-1234567890")
    assert "bedrock-probe-value-1234567890" in registered_secret_values()
