"""Tests for ``aws_bedrock_async_invoke``: the Bedrock video generation runner.

All HTTP is driven through ``httpx.MockTransport`` so no socket is opened (the
test guard blocks real network). Credentials are a tiny fake exposing
``resolve()`` like the real :class:`IamCredentialChain`. The runner is exercised
end to end: a ``StartAsyncInvoke`` submit followed by ``GetAsyncInvoke`` polls
that move from ``InProgress`` to ``Completed`` (or ``Failed``), with the output
MP4 listed and presigned through an injected :class:`S3ObjectStore`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.services.aws_bedrock_async_invoke import (
    VIDEO_MODEL_FAMILIES,
    AsyncInvokeVideoRunner,
    VideoFamilyError,
    resolve_video_family,
    validate_invocation_arn,
)
from app.services.aws_s3_objects import S3ObjectStore
from app.services.aws_sigv4 import SigV4Credentials
from app.services.model_gateway import (
    GenerationOperationOutput,
    GenerationOutput,
    VideoGenerationSubmitRequest,
)
from app.services.provider_resilience import ProviderCallFailure

FIXED_CLOCK = lambda: datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC)  # noqa: E731

OUTPUT_BUCKET = "motionly-video-out"
OUTPUT_PREFIX = "generated/video"
REGION = "us-east-1"
INVOCATION_ARN = "arn:aws:bedrock:us-east-1:123456789012:async-invoke/abc123DEF"

LIST_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Name>motionly-video-out</Name>"
    b"<Contents><Key>generated/video/abc/manifest.json</Key><Size>1</Size></Contents>"
    b"<Contents><Key>generated/video/abc/extra.mp4</Key><Size>2</Size></Contents>"
    b"<Contents><Key>generated/video/abc/output.mp4</Key><Size>3</Size></Contents>"
    b"</ListBucketResult>"
)


class _FakeCredentials:
    def __init__(self, creds: SigV4Credentials | None, *, raises: bool = False) -> None:
        self._creds = creds
        self._raises = raises
        self.calls = 0

    def resolve(self) -> SigV4Credentials:
        self.calls += 1
        if self._raises:
            raise RuntimeError("no IAM credentials found")
        assert self._creds is not None
        return self._creds


def _creds(*, raises: bool = False) -> _FakeCredentials:
    return _FakeCredentials(
        SigV4Credentials(
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ),
        raises=raises,
    )


def _store(handler, *, credentials=None) -> S3ObjectStore:
    return S3ObjectStore(
        region=REGION,
        bucket=OUTPUT_BUCKET,
        credentials=credentials or _creds(),
        provider="aws_bedrock",
        transport=httpx.MockTransport(handler),
        clock=FIXED_CLOCK,
    )


def _runner(
    runtime_handler,
    s3_handler,
    *,
    family: str = "nova_reel",
    credentials=None,
    video_resolution: str = "720p",
    presign_expires_s: int = 900,
) -> AsyncInvokeVideoRunner:
    creds = credentials or _creds()
    return AsyncInvokeVideoRunner(
        region=REGION,
        family=family,
        store=_store(s3_handler, credentials=creds),
        output_prefix=OUTPUT_PREFIX,
        provider="aws_bedrock",
        credentials=creds,
        presign_expires_s=presign_expires_s,
        video_resolution=video_resolution,
        transport=httpx.MockTransport(runtime_handler),
        clock=FIXED_CLOCK,
    )


def _submit_request(
    *,
    duration_s: int = 5,
    ratio: str = "768:1280",
    prompt: str = "a calm sunrise over the sea",
    prompt_image_url: str | None = None,
    idempotency_key: str = "idem-key-1",
) -> VideoGenerationSubmitRequest:
    return VideoGenerationSubmitRequest(
        prompt=prompt,
        prompt_image_url=prompt_image_url,
        duration_s=duration_s,
        ratio=ratio,
        idempotency_key=idempotency_key,
    )


def _no_s3(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
    raise AssertionError(f"unexpected S3 call: {request.url}")


# --- pure helpers ----------------------------------------------------------
def test_video_model_families_table_has_both() -> None:
    assert set(VIDEO_MODEL_FAMILIES) == {"nova_reel", "luma_ray"}


def test_resolve_video_family_unknown_raises() -> None:
    with pytest.raises(VideoFamilyError):
        resolve_video_family("not_a_family")


@pytest.mark.parametrize(
    "arn,ok",
    [
        (INVOCATION_ARN, True),
        ("arn:aws:bedrock:ap-south-1:123456789012:async-invoke/x", True),
        ("arn:aws:bedrock:us-east-1:12345:async-invoke/x", False),
        ("arn:aws:s3:::bucket/key", False),
        ("not-an-arn", False),
        ("", False),
    ],
)
def test_validate_invocation_arn(arn: str, ok: bool) -> None:
    assert validate_invocation_arn(arn) is ok


# --- Nova Reel planning ----------------------------------------------------
def test_nova_short_duration_uses_text_video() -> None:
    captured: dict[str, object] = {}

    def runtime(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["method"] = request.method
        captured["path"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    out = runner.start_video_job("amazon.nova-reel-v1:1", _submit_request(duration_s=5))

    assert isinstance(out, GenerationOperationOutput)
    assert out.operation_id == INVOCATION_ARN
    body = captured["body"]
    assert captured["method"] == "POST"
    assert captured["path"] == "/async-invoke"
    assert body["modelId"] == "amazon.nova-reel-v1:1"
    assert body["modelInput"]["taskType"] == "TEXT_VIDEO"
    assert body["modelInput"]["videoGenerationConfig"]["durationSeconds"] == 6
    assert body["modelInput"]["videoGenerationConfig"]["dimension"] == "1280x720"
    assert body["modelInput"]["videoGenerationConfig"]["fps"] == 24
    seed = body["modelInput"]["videoGenerationConfig"]["seed"]
    assert 0 <= seed <= 2_147_483_646
    assert body["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"] == (
        f"s3://{OUTPUT_BUCKET}/{OUTPUT_PREFIX}"
    )
    # clientRequestToken is a deterministic 64-char hex derived from the key.
    token = body["clientRequestToken"]
    assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)


def test_nova_long_duration_uses_multi_shot_rounded_and_clamped() -> None:
    bodies: list[dict] = []

    def runtime(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    # 7s -> ceil(7/6)*6 = 12 (and >6 so MULTI_SHOT, clamped min 12).
    runner = _runner(runtime, _no_s3, family="nova_reel")
    runner.start_video_job("amazon.nova-reel-v1:1", _submit_request(duration_s=7))
    cfg = bodies[0]["modelInput"]
    assert cfg["taskType"] == "MULTI_SHOT_AUTOMATED"
    assert cfg["videoGenerationConfig"]["durationSeconds"] == 12
    assert "multiShotAutomatedParams" in cfg


def test_nova_seed_is_deterministic_per_idempotency_key() -> None:
    seeds: list[int] = []

    def runtime(request: httpx.Request) -> httpx.Response:
        seeds.append(json.loads(request.content)["modelInput"]["videoGenerationConfig"]["seed"])
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    runner.start_video_job("amazon.nova-reel-v1:1", _submit_request(idempotency_key="k1"))
    runner.start_video_job("amazon.nova-reel-v1:1", _submit_request(idempotency_key="k1"))
    runner.start_video_job("amazon.nova-reel-v1:1", _submit_request(idempotency_key="k2"))
    assert seeds[0] == seeds[1]
    assert seeds[0] != seeds[2]


def test_nova_prompt_over_text_video_limit_is_rejected_not_billed() -> None:
    runner = _runner(_no_s3, _no_s3, family="nova_reel")
    big_prompt = "x" * 513
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job(
            "amazon.nova-reel-v1:1", _submit_request(prompt=big_prompt, duration_s=5)
        )
    info = exc.value.error_info
    assert info.code == "provider_request_rejected"
    assert info.billable is False
    assert info.fallback_eligible is False
    assert info.sent is False


def test_nova_prompt_over_multi_shot_limit_is_rejected() -> None:
    runner = _runner(_no_s3, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job(
            "amazon.nova-reel-v1:1",
            _submit_request(prompt="y" * 4001, duration_s=12),
        )
    assert exc.value.error_info.code == "provider_request_rejected"


# --- Luma Ray planning -----------------------------------------------------
@pytest.mark.parametrize(
    "ratio,aspect",
    [("768:1280", "9:16"), ("1280:768", "16:9")],
)
def test_luma_ratio_maps_to_aspect(ratio: str, aspect: str) -> None:
    captured: dict[str, object] = {}

    def runtime(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    runner = _runner(runtime, _no_s3, family="luma_ray")
    runner.start_video_job("luma.ray-v2:0", _submit_request(ratio=ratio, duration_s=5))
    model_input = captured["body"]["modelInput"]
    assert model_input["aspect_ratio"] == aspect
    assert model_input["duration"] == "5s"
    assert model_input["resolution"] == "720p"
    assert model_input["loop"] is False


def test_luma_long_duration_uses_9s() -> None:
    captured: dict[str, object] = {}

    def runtime(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    runner = _runner(runtime, _no_s3, family="luma_ray", video_resolution="540p")
    runner.start_video_job("luma.ray-v2:0", _submit_request(duration_s=10))
    model_input = captured["body"]["modelInput"]
    assert model_input["duration"] == "9s"
    assert model_input["resolution"] == "540p"


def test_luma_unsupported_ratio_rejected() -> None:
    runner = _runner(_no_s3, _no_s3, family="luma_ray")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job("luma.ray-v2:0", _submit_request(ratio="1:1", duration_s=5))
    assert exc.value.error_info.code == "provider_request_rejected"


# --- prompt_image_url is recorded, never sent ------------------------------
def test_prompt_image_url_is_ignored_and_recorded() -> None:
    captured: dict[str, object] = {}

    def runtime(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    out = runner.start_video_job(
        "amazon.nova-reel-v1:1",
        _submit_request(prompt_image_url="https://example.com/keyframe.png"),
    )
    # Not present anywhere in the body sent to Bedrock.
    assert "keyframe.png" not in json.dumps(captured["body"])
    assert out.raw_response["ignored"]["prompt_image_url"] == (
        "https://example.com/keyframe.png"
    )


# --- submit error handling -------------------------------------------------
def test_submit_malformed_response_without_arn_is_malformed() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "an arn"})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job("amazon.nova-reel-v1:1", _submit_request())
    assert exc.value.error_info.code == "provider_malformed_response"


def test_submit_http_500_surfaces_unavailable() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "busy"})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job("amazon.nova-reel-v1:1", _submit_request())
    assert exc.value.error_info.code == "provider_unavailable"
    assert exc.value.error_info.retryable is True


def test_submit_credential_failure_is_auth() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
        raise AssertionError("should not send")

    runner = _runner(runtime, _no_s3, family="nova_reel", credentials=_creds(raises=True))
    with pytest.raises(ProviderCallFailure) as exc:
        runner.start_video_job("amazon.nova-reel-v1:1", _submit_request())
    assert exc.value.error_info.code == "provider_auth_failed"


# --- poll ------------------------------------------------------------------
def test_check_in_progress_returns_operation_output() -> None:
    captured: dict[str, object] = {}

    def runtime(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.raw_path.decode()
        captured["method"] = request.method
        return httpx.Response(200, json={"status": "InProgress"})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    out = runner.check_video_job(INVOCATION_ARN)
    assert isinstance(out, GenerationOperationOutput)
    assert out.operation_id == INVOCATION_ARN
    assert captured["method"] == "GET"
    # The ARN is singly-encoded in the path (the ':' and '/' are escaped).
    assert captured["path"] == "/async-invoke/" + (
        "arn%3Aaws%3Abedrock%3Aus-east-1%3A123456789012%3Aasync-invoke%2Fabc123DEF"
    )


def test_check_completed_lists_presigns_output_mp4() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "Completed",
                "outputDataConfig": {
                    "s3OutputDataConfig": {
                        "s3Uri": f"s3://{OUTPUT_BUCKET}/generated/video/abc"
                    }
                },
            },
        )

    def s3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=LIST_XML)

    runner = _runner(runtime, s3, family="nova_reel")
    out = runner.check_video_job(INVOCATION_ARN)
    assert isinstance(out, GenerationOutput)
    assert len(out.artifacts) == 1
    url = out.artifacts[0].url
    assert url is not None and url.startswith("https://")
    assert "X-Amz-Signature=" in url
    assert out.raw_response["artifact_key"] == "generated/video/abc/output.mp4"
    assert out.operation_id == INVOCATION_ARN


def test_check_completed_prefers_first_mp4_when_no_output_mp4() -> None:
    list_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Contents><Key>generated/video/abc/clip.mp4</Key></Contents>"
        b"<Contents><Key>generated/video/abc/meta.json</Key></Contents>"
        b"</ListBucketResult>"
    )

    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "Completed"})

    def s3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=list_xml)

    runner = _runner(runtime, s3, family="nova_reel")
    out = runner.check_video_job(INVOCATION_ARN)
    assert isinstance(out, GenerationOutput)
    assert out.raw_response["artifact_key"] == "generated/video/abc/clip.mp4"


def test_check_completed_without_mp4_is_malformed() -> None:
    list_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Contents><Key>generated/video/abc/meta.json</Key></Contents>"
        b"</ListBucketResult>"
    )

    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "Completed"})

    def s3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=list_xml)

    runner = _runner(runtime, s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.check_video_job(INVOCATION_ARN)
    assert exc.value.error_info.code == "provider_malformed_response"


def test_check_failed_is_non_retryable_non_billable() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "Failed", "failureMessage": "content policy"}
        )

    runner = _runner(runtime, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.check_video_job(INVOCATION_ARN)
    info = exc.value.error_info
    assert info.retryable is False
    assert info.billable is False
    assert info.fallback_eligible is False


def test_check_rejects_malformed_arn_before_sending() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
        raise AssertionError("should not send")

    runner = _runner(runtime, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.check_video_job("not-an-arn")
    assert exc.value.error_info.code == "provider_request_rejected"
    assert exc.value.error_info.sent is False


def test_check_unknown_status_is_malformed() -> None:
    def runtime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "Mystery"})

    runner = _runner(runtime, _no_s3, family="nova_reel")
    with pytest.raises(ProviderCallFailure) as exc:
        runner.check_video_job(INVOCATION_ARN)
    assert exc.value.error_info.code == "provider_malformed_response"


# --- full submit + poll round trip -----------------------------------------
def test_submit_then_poll_to_completion_end_to_end() -> None:
    states = iter(["InProgress", "Completed"])

    def runtime(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})
        status = next(states)
        body: dict[str, object] = {"status": status}
        if status == "Completed":
            body["outputDataConfig"] = {
                "s3OutputDataConfig": {
                    "s3Uri": f"s3://{OUTPUT_BUCKET}/generated/video/abc"
                }
            }
        return httpx.Response(200, json=body)

    def s3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=LIST_XML)

    runner = _runner(runtime, s3, family="nova_reel")
    submitted = runner.start_video_job("amazon.nova-reel-v1:1", _submit_request())
    assert isinstance(submitted, GenerationOperationOutput)

    running = runner.check_video_job(submitted.operation_id)
    assert isinstance(running, GenerationOperationOutput)

    done = runner.check_video_job(submitted.operation_id)
    assert isinstance(done, GenerationOutput)
    assert done.artifacts[0].url.startswith("https://")
