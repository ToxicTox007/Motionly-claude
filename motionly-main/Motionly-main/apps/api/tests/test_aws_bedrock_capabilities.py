"""Amazon Bedrock adapter dispatch for the CPU-only capabilities (spec item E).

These tests drive :class:`AwsBedrockAdapter` built through
:func:`aws_bedrock_factory` for the two new apis (``data_automation`` and
``async_invoke``) and the extended ``converse`` api (object detection + data-URL
captions). All HTTP — the Bedrock runtime, the BDA runtime and the S3 staging
reads/writes — is driven through ``httpx.MockTransport`` so no socket opens (the
test guard blocks real network). The IAM chain reads its env var credentials
from an injected mapping, so no real AWS lookup happens either.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.services.aws_bedrock_adapter import (
    AwsBedrockAdapter,
    BedrockConfig,
    BedrockConfigError,
    aws_bedrock_factory,
    validate_bedrock_config,
)
from app.services.model_gateway import (
    ChatMessage,
    DiarizationOutput,
    DiarizationRequest,
    GenerationOperationOutput,
    GenerationOutput,
    ModelCapability,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    PlanningRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import ProviderCallFailure

FIXED_CLOCK = lambda: datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC)  # noqa: E731

REGION = "us-east-1"
BDA_REGION = "ap-south-1"
STAGING_BUCKET = "motionly-bda-staging"
VIDEO_BUCKET = "motionly-nova-reel-output"
PROFILE_ARN = "arn:aws:bedrock:ap-south-1:123456789012:data-automation-profile/motionly-audio"
PROJECT_ARN = (
    "arn:aws:bedrock:ap-south-1:123456789012:data-automation-project/motionly-audio-speaker-labels"
)
INVOCATION_ARN = "arn:aws:bedrock:us-east-1:123456789012:async-invoke/abc123DEF"

# A one-pixel JPEG header the vision sniffer recognises (FFD8FF).
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01fake-image-body"

ENV = {
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}

BDA_STANDARD_OUTPUT = {
    "metadata": {"duration_millis": 2000, "dominant_asset_language": "EN"},
    "audio_items": [
        {"content": "Hello", "start_timestamp_millis": 0, "end_timestamp_millis": 500},
        {"content": "world", "start_timestamp_millis": 500, "end_timestamp_millis": 900},
    ],
    "audio_segments": [
        {
            "type": "TRANSCRIPT",
            "text": "Hello world",
            "start_timestamp_millis": 0,
            "end_timestamp_millis": 900,
            "speaker": {"speaker_label": "spk_0"},
        }
    ],
}
JOB_METADATA = {
    "output_metadata": [
        {
            "segment_metadata": [
                {"standard_output_path": f"s3://{STAGING_BUCKET}/audio/output/tok/result.json"}
            ]
        }
    ]
}


class _Transport:
    """A single MockTransport handler for every Bedrock + S3 host in the suite."""

    def __init__(
        self,
        *,
        converse_payload=None,
        bda_statuses=None,
        video_submit=None,
        video_statuses=None,
        s3_objects=None,
    ) -> None:
        self.converse_payload = converse_payload
        self.bda_statuses = list(bda_statuses or [])
        self.video_submit = video_submit
        self.video_statuses = list(video_statuses or [])
        self.s3_objects = dict(s3_objects or {})
        self.converse_bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host.startswith("bedrock-data-automation-runtime"):
            return self._bda(request)
        if host.startswith("bedrock-runtime"):
            return self._runtime(request)
        return self._s3(request)

    def _runtime(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/converse"):
            self.converse_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=self.converse_payload)
        if path == "/async-invoke" and request.method == "POST":
            return httpx.Response(200, json=self.video_submit)
        if path.startswith("/async-invoke/") and request.method == "GET":
            return httpx.Response(200, json=self.video_statuses.pop(0))
        raise AssertionError(f"unexpected runtime call {request.method} {path}")

    def _bda(self, request: httpx.Request) -> httpx.Response:
        target = request.headers.get("x-amz-target", "")
        if target.endswith("InvokeDataAutomationAsync"):
            return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})
        if target.endswith("GetDataAutomationStatus"):
            return httpx.Response(200, json=self.bda_statuses.pop(0))
        raise AssertionError(f"unexpected BDA target {target!r}")

    def _s3(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "PUT":
            self.s3_objects[path] = request.content
            return httpx.Response(200)
        if method == "DELETE":
            self.s3_objects.pop(path, None)
            return httpx.Response(204)
        if method == "GET" and request.url.params.get("list-type") == "2":
            prefix = request.url.params.get("prefix", "")
            keys = [p.lstrip("/") for p in self.s3_objects if p.lstrip("/").startswith(prefix)]
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                + "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
                + "</ListBucketResult>"
            )
            return httpx.Response(200, content=body.encode())
        if method == "GET":
            data = self.s3_objects.get(path)
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        raise AssertionError(f"unexpected S3 {method} {path}")


def _adapter(config: BedrockConfig, transport: httpx.MockTransport) -> AwsBedrockAdapter:
    return aws_bedrock_factory(
        config=config,
        env=ENV,
        provider="aws_bedrock",
        credential_transport=transport,
        bedrock_transport=transport,
        clock=FIXED_CLOCK,
    )


def _route(model: str, capability: ModelCapability = ModelCapability.VISION) -> ModelRoute:
    return ModelRoute(
        route_id="r1",
        capability=capability,
        adapter="aws_bedrock",
        provider="aws_bedrock",
        model=model,
        model_revision="2026-01-01",
    )


# --- validation ------------------------------------------------------------
def test_validate_data_automation_requires_profile_and_bucket() -> None:
    errors = validate_bedrock_config(
        region=BDA_REGION, api="data_automation", embedding_family=None, timeout_s=60
    )
    assert any("s3_bucket is required" in e for e in errors)
    assert any("data_automation_profile_arn" in e for e in errors)


def test_validate_async_invoke_requires_family_and_bucket() -> None:
    errors = validate_bedrock_config(
        region=REGION, api="async_invoke", embedding_family=None, timeout_s=60
    )
    assert any("s3_bucket is required" in e for e in errors)
    assert any("video_family" in e for e in errors)


def test_validate_rejects_bad_bucket_and_family() -> None:
    errors = validate_bedrock_config(
        region=REGION,
        api="async_invoke",
        embedding_family=None,
        timeout_s=60,
        s3_bucket="A_BAD_Bucket",
        video_family="not_a_family",
    )
    assert any("s3_bucket invalid" in e for e in errors)
    assert any("video_family must be one of" in e for e in errors)


def test_converse_config_unchanged_and_backward_compatible() -> None:
    # The original keyword-only signature still validates a converse config.
    assert (
        validate_bedrock_config(region=REGION, api="converse", embedding_family=None, timeout_s=60)
        == []
    )
    config = BedrockConfig(region=REGION, api="converse")
    assert config.api == "converse"


def test_config_rejects_unknown_api() -> None:
    with pytest.raises(BedrockConfigError):
        BedrockConfig(region=REGION, api="bogus")


def _data_automation_config() -> BedrockConfig:
    return BedrockConfig(
        region=BDA_REGION,
        api="data_automation",
        s3_bucket=STAGING_BUCKET,
        s3_prefix="audio",
        s3_region=BDA_REGION,
        data_automation_profile_arn=PROFILE_ARN,
        data_automation_project_arn=PROJECT_ARN,
        poll_interval_s=0.0,
        max_wait_s=100.0,
    )


def _video_config() -> BedrockConfig:
    return BedrockConfig(
        region=REGION,
        api="async_invoke",
        s3_bucket=VIDEO_BUCKET,
        s3_prefix="video",
        s3_region=REGION,
        video_family="nova_reel",
    )


# --- converse: object detection -------------------------------------------
def _detection_payload(text: str) -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }


def test_object_detection_one_call_per_image_sums_usage() -> None:
    text = '[{"label":"cat","box":[0,0,500,500],"score":0.9}]'
    transport = httpx.MockTransport(_Transport(converse_payload=_detection_payload(text)).handler)
    adapter = _adapter(BedrockConfig(region=REGION, api="converse"), transport)
    request = ObjectDetectionRequest(images=(JPEG_BYTES, JPEG_BYTES), max_results=5)
    result = adapter.invoke(route=_route("amazon.nova-lite-v1:0"), request=request)
    assert isinstance(result.output, ObjectDetectionOutput)
    assert len(result.output.detections) == 2
    assert result.output.detections[0][0].label == "cat"
    # Two Converse calls, so usage sums both.
    assert result.usage.input_units == 20
    assert result.usage.output_units == 10


def test_object_detection_per_image_unparsable_is_empty_tuple() -> None:
    # One image parses, one does not -> the unparsable one is an empty tuple.
    good = _detection_payload('[{"label":"dog","box":[0,0,100,100],"score":0.5}]')
    bad = _detection_payload("I cannot find any objects, sorry!")
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/converse"):
            payload = good if state["n"] == 0 else bad
            state["n"] += 1
            return httpx.Response(200, json=payload)
        raise AssertionError("only converse expected")

    adapter = _adapter(BedrockConfig(region=REGION, api="converse"), httpx.MockTransport(handler))
    request = ObjectDetectionRequest(images=(JPEG_BYTES, JPEG_BYTES), max_results=5)
    result = adapter.invoke(route=_route("amazon.nova-lite-v1:0"), request=request)
    assert result.output.detections[0][0].label == "dog"
    assert result.output.detections[1] == ()


def test_object_detection_all_unparsable_is_malformed() -> None:
    bad = _detection_payload("no json here at all")
    transport = httpx.MockTransport(_Transport(converse_payload=bad).handler)
    adapter = _adapter(BedrockConfig(region=REGION, api="converse"), transport)
    request = ObjectDetectionRequest(images=(JPEG_BYTES,), max_results=5)
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(route=_route("amazon.nova-lite-v1:0"), request=request)
    assert exc.value.error_info.code == "provider_malformed_response"


def test_object_detection_empty_array_is_valid_nothing_detected() -> None:
    transport = httpx.MockTransport(_Transport(converse_payload=_detection_payload("[]")).handler)
    adapter = _adapter(BedrockConfig(region=REGION, api="converse"), transport)
    request = ObjectDetectionRequest(images=(JPEG_BYTES,), max_results=5)
    result = adapter.invoke(route=_route("amazon.nova-lite-v1:0"), request=request)
    assert result.output.detections == ((),)


# --- converse: data-URL captions ------------------------------------------
def test_caption_converts_data_url_to_bytes_block() -> None:
    reply = {"output": {"message": {"content": [{"text": "a cat"}]}}, "usage": {}}
    tracker = _Transport(converse_payload=reply)
    adapter = _adapter(
        BedrockConfig(region=REGION, api="converse"), httpx.MockTransport(tracker.handler)
    )
    data_url = "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode()
    request = VisionRequest(prompt="describe", image_urls=(data_url,))
    result = adapter.invoke(route=_route("x"), request=request)
    assert isinstance(result.output, VisionOutput)
    # The data URL became an inline bytes image block, not a by-reference url.
    content = tracker.converse_bodies[0]["messages"][0]["content"]
    image_blocks = [b for b in content if "image" in b]
    assert image_blocks and "bytes" in image_blocks[0]["image"]["source"]
    assert "url" not in image_blocks[0]["image"]["source"]


def test_caption_keeps_https_url_by_reference() -> None:
    reply = {"output": {"message": {"content": [{"text": "a dog"}]}}, "usage": {}}
    tracker = _Transport(converse_payload=reply)
    adapter = _adapter(
        BedrockConfig(region=REGION, api="converse"), httpx.MockTransport(tracker.handler)
    )
    request = VisionRequest(prompt="describe", image_urls=("https://example.invalid/i.jpg",))
    adapter.invoke(route=_route("x"), request=request)
    content = tracker.converse_bodies[0]["messages"][0]["content"]
    image_blocks = [b for b in content if "image" in b]
    assert image_blocks[0]["image"]["source"]["url"] == "https://example.invalid/i.jpg"


# --- data_automation: transcription + diarization -------------------------
def _bda_transport() -> _Transport:
    return _Transport(
        bda_statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{STAGING_BUCKET}/audio/output/tok/job_metadata.json"
                },
            }
        ],
        s3_objects={
            "/audio/output/tok/job_metadata.json": json.dumps(JOB_METADATA).encode(),
            "/audio/output/tok/result.json": json.dumps(BDA_STANDARD_OUTPUT).encode(),
        },
    )


def test_data_automation_transcription(tmp_path: Path) -> None:
    transport = _bda_transport()
    adapter = _adapter(_data_automation_config(), httpx.MockTransport(transport.handler))
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfake")
    request = TranscriptionRequest(
        audio_path=audio, language=None, prompt=None, response_format="json"
    )
    result = adapter.invoke(route=_route("x", ModelCapability.TRANSCRIPTION), request=request)
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.text == "Hello world"
    assert result.output.language == "en"
    assert tuple(w.text for w in result.output.words) == ("Hello", "world")
    # Usage = ceil(duration seconds) = ceil(2.0) = 2.
    assert result.usage.input_units == 2


def test_data_automation_diarization(tmp_path: Path) -> None:
    transport = _bda_transport()
    adapter = _adapter(_data_automation_config(), httpx.MockTransport(transport.handler))
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfake")
    request = DiarizationRequest(audio_path=audio, duration_s=2.0)
    result = adapter.invoke(route=_route("x", ModelCapability.AUDIO), request=request)
    assert isinstance(result.output, DiarizationOutput)
    assert result.output.segments[0].speaker == "spk_0"


def test_data_automation_no_speaker_labels_is_malformed(tmp_path: Path) -> None:
    output = {
        "metadata": {"duration_millis": 1000},
        "audio_segments": [
            {
                "type": "TRANSCRIPT",
                "text": "hi",
                "start_timestamp_millis": 0,
                "end_timestamp_millis": 10,
            }
        ],
    }
    transport = _Transport(
        bda_statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{STAGING_BUCKET}/audio/output/tok/job_metadata.json"
                },
            }
        ],
        s3_objects={
            "/audio/output/tok/job_metadata.json": json.dumps(JOB_METADATA).encode(),
            "/audio/output/tok/result.json": json.dumps(output).encode(),
        },
    )
    adapter = _adapter(_data_automation_config(), httpx.MockTransport(transport.handler))
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfake")
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route("x", ModelCapability.AUDIO),
            request=DiarizationRequest(audio_path=audio, duration_s=1.0),
        )
    assert exc.value.error_info.code == "provider_malformed_response"


def test_data_automation_rejects_unsupported_suffix(tmp_path: Path) -> None:
    adapter = _adapter(_data_automation_config(), httpx.MockTransport(_bda_transport().handler))
    audio = tmp_path / "clip.txt"
    audio.write_bytes(b"not audio")
    request = TranscriptionRequest(
        audio_path=audio, language=None, prompt=None, response_format="json"
    )
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(route=_route("x", ModelCapability.TRANSCRIPTION), request=request)
    assert exc.value.error_info.sent is False
    assert exc.value.error_info.billable is False


# --- async_invoke: submit + poll ------------------------------------------
def test_async_invoke_submit_and_poll_complete() -> None:
    transport = _Transport(
        video_submit={"invocationArn": INVOCATION_ARN},
        video_statuses=[
            {"status": "InProgress"},
            {
                "status": "Completed",
                "outputDataConfig": {"s3OutputDataConfig": {"s3Uri": f"s3://{VIDEO_BUCKET}/video"}},
            },
        ],
        s3_objects={"/video/output.mp4": b"mp4-bytes"},
    )
    mock = httpx.MockTransport(transport.handler)
    adapter = _adapter(_video_config(), mock)
    route = _route("amazon.nova-reel-v1:1", ModelCapability.GENERATION)
    submit = adapter.invoke(
        route=route,
        request=VideoGenerationSubmitRequest(
            prompt="a sunrise",
            prompt_image_url=None,
            duration_s=5,
            ratio="768:1280",
            idempotency_key="idem-1",
        ),
    )
    assert isinstance(submit.output, GenerationOperationOutput)
    op_id = submit.output.operation_id
    assert op_id == INVOCATION_ARN

    running = adapter.invoke(route=route, request=VideoGenerationPollRequest(operation_id=op_id))
    assert isinstance(running.output, GenerationOperationOutput)

    done = adapter.invoke(route=route, request=VideoGenerationPollRequest(operation_id=op_id))
    assert isinstance(done.output, GenerationOutput)
    assert done.output.artifacts[0].url.startswith("https://")
    assert "output.mp4" in done.output.artifacts[0].url


def test_async_invoke_submit_records_ignored_prompt_image_url() -> None:
    transport = _Transport(video_submit={"invocationArn": INVOCATION_ARN})
    adapter = _adapter(_video_config(), httpx.MockTransport(transport.handler))
    submit = adapter.invoke(
        route=_route("amazon.nova-reel-v1:1", ModelCapability.GENERATION),
        request=VideoGenerationSubmitRequest(
            prompt="x",
            prompt_image_url="https://example.invalid/i.png",
            duration_s=5,
            ratio="768:1280",
            idempotency_key="idem-2",
        ),
    )
    assert submit.output.raw_response["ignored"]["prompt_image_url"] == (
        "https://example.invalid/i.png"
    )


# --- dispatch guards -------------------------------------------------------
def test_data_automation_rejects_wrong_request_type(tmp_path: Path) -> None:
    adapter = _adapter(_data_automation_config(), httpx.MockTransport(_bda_transport().handler))
    with pytest.raises(TypeError):
        adapter.invoke(
            route=_route("x"),
            request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
        )


def test_async_invoke_rejects_wrong_request_type() -> None:
    adapter = _adapter(_video_config(), httpx.MockTransport(_Transport().handler))
    with pytest.raises(TypeError):
        adapter.invoke(
            route=_route("x"),
            request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
        )
