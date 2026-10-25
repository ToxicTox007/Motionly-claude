"""End-to-end CPU-only Bedrock registry (spec item E).

Proves the ``docs/ai-registry/examples/aws-bedrock-cpu.json`` example and
``infra/ai-registry/cpu-local.json`` validate *together* in production with
``GPU_WORKERS_ENABLED=false``, build one runtime with fake env AWS credentials,
report planning, transcription, diarization, object detection, stem separation
and video generation as available, and drive transcription, diarization, object
detection and a video submit+poll end-to-end through the real gateway.

All HTTP (Bedrock runtime, the BDA runtime and S3) is served by one
``httpx.MockTransport`` so no socket opens (the test guard blocks real network),
and the IAM chain reads its credentials from monkeypatched env vars.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.services.inference_registry import load_registry_sources, validate_registry
from app.services.inference_runtime import build_inference_runtime, validate_mapping
from app.services.model_gateway import (
    DiarizationOutput,
    DiarizationRequest,
    GenerationOperationOutput,
    GenerationOutput,
    ModelCallContext,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "docs" / "ai-registry" / "examples" / "aws-bedrock-cpu.json"
CPU_LOCAL = ROOT / "infra" / "ai-registry" / "cpu-local.json"

STAGING_BUCKET = "motionly-bda-staging"
VIDEO_BUCKET = "motionly-nova-reel-output"
INVOCATION_ARN = "arn:aws:bedrock:us-east-1:123456789012:async-invoke/cpuExample01"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01fake-image-body"

BDA_OUTPUT = {
    "metadata": {"duration_millis": 3000, "dominant_asset_language": "EN"},
    "audio_items": [
        {"content": "ready", "start_timestamp_millis": 0, "end_timestamp_millis": 500},
        {"content": "set", "start_timestamp_millis": 600, "end_timestamp_millis": 900},
    ],
    "audio_segments": [
        {
            "type": "TRANSCRIPT",
            "text": "ready set",
            "start_timestamp_millis": 0,
            "end_timestamp_millis": 900,
            "speaker": {"speaker_label": "spk_0"},
        },
        {
            "type": "TRANSCRIPT",
            "text": "go",
            "start_timestamp_millis": 1000,
            "end_timestamp_millis": 1400,
            "speaker": {"speaker_label": "spk_1"},
        },
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


class _CpuBedrockServer:
    """One MockTransport handler covering every host the example contacts."""

    def __init__(self) -> None:
        self.s3_objects: dict[str, bytes] = {
            "/audio/output/tok/job_metadata.json": json.dumps(JOB_METADATA).encode(),
            "/audio/output/tok/result.json": json.dumps(BDA_OUTPUT).encode(),
            "/video/output.mp4": b"mp4-bytes",
        }
        # Two polls: first InProgress, then Completed.
        self.video_statuses = [
            {"status": "InProgress"},
            {
                "status": "Completed",
                "outputDataConfig": {
                    "s3OutputDataConfig": {"s3Uri": f"s3://{VIDEO_BUCKET}/video"}
                },
            },
        ]

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
            text = '[{"label":"person","box":[100,100,400,600],"score":0.8}]'
            return httpx.Response(
                200,
                json={
                    "output": {"message": {"content": [{"text": text}]}},
                    "usage": {"inputTokens": 12, "outputTokens": 8},
                },
            )
        if path == "/async-invoke" and request.method == "POST":
            return httpx.Response(200, json={"invocationArn": INVOCATION_ARN})
        if path.startswith("/async-invoke/") and request.method == "GET":
            return httpx.Response(200, json=self.video_statuses.pop(0))
        raise AssertionError(f"unexpected runtime call {request.method} {path}")

    def _bda(self, request: httpx.Request) -> httpx.Response:
        target = request.headers.get("x-amz-target", "")
        if target.endswith("InvokeDataAutomationAsync"):
            return httpx.Response(200, json={"invocationArn": "bda-arn"})
        if target.endswith("GetDataAutomationStatus"):
            return httpx.Response(
                200,
                json={
                    "status": "Success",
                    "outputConfiguration": {
                        "s3Uri": f"s3://{STAGING_BUCKET}/audio/output/tok/job_metadata.json"
                    },
                },
            )
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
            keys = [
                p.lstrip("/")
                for p in self.s3_objects
                if p.lstrip("/").startswith(prefix)
            ]
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


def _settings(**updates):
    from app.core.config import get_settings

    spec = f"{EXAMPLE},{CPU_LOCAL}"
    values = {
        "app_env": "production",
        "ai_registry_file": spec,
        "gpu_workers_enabled": False,
        "redis_url": None,
        "supabase_url": None,
        "supabase_service_role_key": None,
    }
    values.update(updates)
    return get_settings().model_copy(update=values)


def _set_fake_aws_env(monkeypatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", raising=False)


# --- validation: the two fragments validate together in production ---------
def test_example_and_cpu_local_validate_in_production() -> None:
    doc, load_diags = load_registry_sources(f"{EXAMPLE},{CPU_LOCAL}")
    assert [d for d in load_diags if d.is_error] == []
    validation = validate_registry(
        doc,
        app_env="production",
        gpu_workers_enabled=False,
        block_noncommercial=False,
        mapping_validator=validate_mapping,
    )
    errors = [d for d in validation.diagnostics if d.is_error]
    assert errors == [], errors
    assert {
        "bedrock-planning-chat",
        "bedrock-transcription",
        "bedrock-diarization",
        "bedrock-object-detection",
        "bedrock-video-generation",
        "local-stem-separation",
    } <= set(validation.valid_route_ids)


def _runtime(monkeypatch) -> tuple[object, _CpuBedrockServer]:
    _set_fake_aws_env(monkeypatch)
    server = _CpuBedrockServer()
    runtime = build_inference_runtime(
        _settings(), transport=httpx.MockTransport(server.handler)
    )
    return runtime, server


def test_runtime_reports_all_capabilities_available(monkeypatch) -> None:
    runtime, _server = _runtime(monkeypatch)
    capabilities = {r.capability.value for r in runtime.route_records.values()}
    assert {
        "planning.chat",
        "transcription.speech_to_text",
        "audio.diarization",
        "vision.object_detection",
        "audio.stem_separation",
        "video.generation.submit",
    } <= capabilities


def test_transcription_through_the_gateway(monkeypatch, tmp_path: Path) -> None:
    runtime, _server = _runtime(monkeypatch)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfake-audio")
    result = runtime.gateway.invoke(
        TranscriptionRequest(
            audio_path=audio, language=None, prompt=None, response_format="json"
        ),
        context=ModelCallContext(idempotency_key="k-transcribe"),
    )
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.text == "ready set go"
    assert result.output.language == "en"


def test_diarization_through_the_gateway(monkeypatch, tmp_path: Path) -> None:
    runtime, _server = _runtime(monkeypatch)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfake-audio")
    result = runtime.gateway.invoke(
        DiarizationRequest(audio_path=audio, duration_s=3.0),
        context=ModelCallContext(idempotency_key="k-diar"),
    )
    assert isinstance(result.output, DiarizationOutput)
    assert result.output.speaker_count == 2


def test_object_detection_through_the_gateway(monkeypatch) -> None:
    runtime, _server = _runtime(monkeypatch)
    result = runtime.gateway.invoke(
        ObjectDetectionRequest(images=(JPEG_BYTES,), max_results=8),
        context=ModelCallContext(idempotency_key="k-detect"),
    )
    assert isinstance(result.output, ObjectDetectionOutput)
    assert result.output.detections[0][0].label == "person"


def test_video_submit_and_poll_through_the_gateway(monkeypatch) -> None:
    runtime, _server = _runtime(monkeypatch)
    # Find the submit route so the route-affine poll binds to it.
    route = next(
        r for r in runtime.gateway.routes
        if r.capability_id == "video.generation.submit"
    )
    submit = runtime.gateway.invoke(
        VideoGenerationSubmitRequest(
            prompt="a calm sunrise over the sea",
            prompt_image_url=None,
            duration_s=5,
            ratio="768:1280",
            idempotency_key="k-video",
        ),
        context=ModelCallContext(idempotency_key="k-video"),
    )
    assert isinstance(submit.output, GenerationOperationOutput)
    op_id = submit.output.operation_id
    assert op_id == INVOCATION_ARN

    poll_context = ModelCallContext(
        idempotency_key="k-video",
        required_route_id=route.route_id,
        required_model_revision=route.model_revision,
    )
    running = runtime.gateway.invoke(
        VideoGenerationPollRequest(operation_id=op_id), context=poll_context
    )
    assert isinstance(running.output, GenerationOperationOutput)
    done = runtime.gateway.invoke(
        VideoGenerationPollRequest(operation_id=op_id), context=poll_context
    )
    assert isinstance(done.output, GenerationOutput)
    assert done.output.artifacts[0].url.startswith("https://")
    assert "output.mp4" in done.output.artifacts[0].url
