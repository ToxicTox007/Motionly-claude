"""Tests for ``aws_bedrock_data_automation``: the BDA audio runner, no network.

All HTTP (both the BDA runtime control plane and the S3 staging reads/writes) is
driven through ``httpx.MockTransport`` so no socket opens (the test guard blocks
real network). Credentials are a tiny fake exposing ``resolve()`` like the real
:class:`IamCredentialChain`. ``sleep`` and ``monotonic`` are injected so the poll
loop never really waits.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.services import aws_bedrock_data_automation as bda
from app.services.aws_bedrock_data_automation import (
    DataAutomationAudioRunner,
    DataAutomationOutputError,
    DataAutomationSettings,
    diarization_from_result,
    duration_seconds_from_result,
    transcription_from_result,
    usage_input_units_from_result,
    validate_data_automation_arns,
)
from app.services.aws_s3_objects import S3ObjectStore
from app.services.aws_sigv4 import SigV4Credentials
from app.services.provider_resilience import ProviderCallFailure

FIXED_CLOCK = lambda: datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC)  # noqa: E731

PROFILE_ARN = (
    "arn:aws:bedrock:ap-south-1:123456789012:data-automation-profile/default-v1"
)
PROJECT_ARN = (
    "arn:aws:bedrock:ap-south-1:123456789012:data-automation-project/motionly-audio"
)
INVOCATION_ARN = (
    "arn:aws:bedrock:ap-south-1:123456789012:data-automation-invocation/abc123"
)
BUCKET = "motionly-staging"

# A representative BDA audio standard output (user-guide shape).
STANDARD_OUTPUT = {
    "metadata": {
        "semantic_modality": "AUDIO",
        "duration_millis": 1970,
        "format": "wav",
        "dominant_asset_language": "EN",
    },
    "audio_items": [
        {
            "item_index": 0,
            "content": "Auto",
            "start_timestamp_millis": 9,
            "end_timestamp_millis": 119,
        },
        {
            "item_index": 1,
            "content": "sales",
            "start_timestamp_millis": 120,
            "end_timestamp_millis": 400,
        },
        # An invalid item (missing timestamps) is skipped, not fatal.
        {"item_index": 2, "content": "dropped"},
    ],
    "audio_segments": [
        {
            "start_timestamp_millis": 1000,
            "end_timestamp_millis": 1970,
            "segment_index": 1,
            "type": "TRANSCRIPT",
            "text": "How can I help you?",
            "speaker": {"speaker_label": "spk_1"},
        },
        {
            "start_timestamp_millis": 0,
            "end_timestamp_millis": 999,
            "segment_index": 0,
            "type": "TRANSCRIPT",
            "text": "Auto sales, Cherry speaking.",
            "speaker": {"speaker_label": "spk_0"},
        },
    ],
}

JOB_METADATA = {
    "output_metadata": [
        {
            "segment_metadata": [
                {
                    "standard_output_path": f"s3://{BUCKET}/p/output/tok/0/result.json"
                }
            ]
        }
    ]
}


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


def _settings(**overrides) -> DataAutomationSettings:
    base = {
        "region": "ap-south-1",
        "profile_arn": PROFILE_ARN,
        "s3_bucket": BUCKET,
        "s3_prefix": "p",
        "s3_region": "ap-south-1",
        "project_arn": PROJECT_ARN,
        "poll_interval_s": 1.0,
        "max_poll_interval_s": 4.0,
        "max_wait_s": 100.0,
    }
    base.update(overrides)
    return DataAutomationSettings(**base)


class _Scenario:
    """A single MockTransport handler covering both the BDA host and S3 host.

    ``statuses`` is the sequence of GetDataAutomationStatus payloads returned in
    order; the invoke call returns ``invoke_payload``. S3 PUT/DELETE/GET/LIST are
    answered from an in-memory object map keyed by request path.
    """

    def __init__(
        self,
        *,
        statuses,
        invoke_payload=None,
        objects=None,
        invoke_status=200,
    ) -> None:
        self._statuses = list(statuses)
        self._invoke_payload = (
            invoke_payload
            if invoke_payload is not None
            else {"invocationArn": INVOCATION_ARN}
        )
        self._invoke_status = invoke_status
        self._objects = dict(objects or {})
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.targets: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host.startswith("bedrock-data-automation-runtime"):
            return self._bda(request)
        # S3 virtual-hosted host.
        return self._s3(request)

    def _bda(self, request: httpx.Request) -> httpx.Response:
        target = request.headers.get("x-amz-target", "")
        self.targets.append(target)
        assert request.headers["content-type"] == "application/x-amz-json-1.1"
        if target.endswith("InvokeDataAutomationAsync"):
            return httpx.Response(self._invoke_status, json=self._invoke_payload)
        if target.endswith("GetDataAutomationStatus"):
            payload = self._statuses.pop(0)
            return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected target {target!r}")

    def _s3(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "PUT":
            self.puts.append(path)
            self._objects[path] = request.content
            return httpx.Response(200)
        if method == "DELETE":
            self.deletes.append(path)
            self._objects.pop(path, None)
            return httpx.Response(204)
        if method == "GET" and request.url.params.get("list-type") == "2":
            prefix = request.url.params.get("prefix", "")
            keys = [
                p.lstrip("/")
                for p in self._objects
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
            data = self._objects.get(path)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)
        raise AssertionError(f"unexpected S3 {method} {path}")


def _store(scenario: _Scenario, credentials) -> S3ObjectStore:
    return S3ObjectStore(
        region="ap-south-1",
        bucket=BUCKET,
        credentials=credentials,
        provider="aws_bedrock",
        transport=httpx.MockTransport(scenario.handler),
        clock=FIXED_CLOCK,
    )


def _runner(scenario: _Scenario, *, settings=None, credentials=None):
    creds = credentials or _creds()
    store = _store(scenario, creds)
    return DataAutomationAudioRunner(
        settings or _settings(),
        credentials=creds,
        provider="aws_bedrock",
        store=store,
        transport=httpx.MockTransport(scenario.handler),
        clock=FIXED_CLOCK,
        sleep=lambda _s: None,
        monotonic=_FakeMonotonic(),
    )


class _FakeMonotonic:
    """A monotonic clock that advances 1s each call so deadlines are reachable."""

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1.0
        return self._t


def _audio_file(tmp_path: Path, suffix: str = ".wav") -> Path:
    path = tmp_path / f"clip{suffix}"
    path.write_bytes(b"RIFF....WAVEfake-audio-bytes")
    return path


# --- ARN validation --------------------------------------------------------
def test_validate_arns_accepts_profile_and_project() -> None:
    assert validate_data_automation_arns(
        profile_arn=PROFILE_ARN, project_arn=PROJECT_ARN
    ) == []
    assert validate_data_automation_arns(profile_arn=PROFILE_ARN, project_arn=None) == []


@pytest.mark.parametrize(
    ("profile", "project"),
    [
        ("", PROJECT_ARN),
        ("not-an-arn", None),
        (PROFILE_ARN, "arn:aws:bedrock:ap-south-1:123456789012:something/else"),
        (PROJECT_ARN, None),  # a project ARN is not a valid profile ARN
    ],
)
def test_validate_arns_reports_problems(profile, project) -> None:
    assert validate_data_automation_arns(profile_arn=profile, project_arn=project) != []


# --- run_audio_analysis end-to-end -----------------------------------------
def test_run_audio_analysis_success(tmp_path: Path) -> None:
    scenario = _Scenario(
        statuses=[
            {"status": "InProgress"},
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                },
            },
        ],
    )
    # Seed the two JSON artifacts the runner will read back.
    scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
        JOB_METADATA
    ).encode()
    scenario._objects["/p/output/tok/0/result.json"] = json.dumps(
        STANDARD_OUTPUT
    ).encode()

    runner = _runner(scenario)
    result = runner.run_audio_analysis(
        _audio_file(tmp_path), idempotency_scope="ws:proj"
    )
    assert result["metadata"]["dominant_asset_language"] == "EN"
    # Input was uploaded and cleanup removed staged objects.
    assert any("/p/input/" in p for p in scenario.puts)
    assert scenario.deletes  # best-effort cleanup ran
    # Both BDA targets were invoked with the right x-amz-target values.
    assert any(t.endswith("InvokeDataAutomationAsync") for t in scenario.targets)
    assert any(t.endswith("GetDataAutomationStatus") for t in scenario.targets)


def test_run_audio_analysis_is_deterministic_idempotent(tmp_path: Path) -> None:
    audio = _audio_file(tmp_path)

    def make():
        scenario = _Scenario(
            statuses=[
                {
                    "status": "Success",
                    "outputConfiguration": {
                        "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                    },
                }
            ],
        )
        scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
            JOB_METADATA
        ).encode()
        scenario._objects["/p/output/tok/0/result.json"] = json.dumps(
            STANDARD_OUTPUT
        ).encode()
        return scenario

    s1 = make()
    _runner(s1).run_audio_analysis(audio, idempotency_scope="ws:proj")
    s2 = make()
    _runner(s2).run_audio_analysis(audio, idempotency_scope="ws:proj")
    # The input key (clientToken-derived) is identical across retries.
    assert s1.puts == s2.puts


def test_run_audio_analysis_rejects_unsupported_suffix_before_send(
    tmp_path: Path,
) -> None:
    scenario = _Scenario(statuses=[])
    runner = _runner(scenario)
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(
            _audio_file(tmp_path, suffix=".txt"), idempotency_scope="ws:proj"
        )
    info = excinfo.value.error_info
    assert info.code == "provider_request_rejected"
    assert info.billable is False
    assert info.sent is False
    # Nothing was uploaded or invoked.
    assert scenario.puts == []
    assert scenario.targets == []


def test_run_audio_analysis_client_error_is_rejected_not_billed(tmp_path: Path) -> None:
    scenario = _Scenario(
        statuses=[{"status": "ClientError", "errorMessage": "bad input"}]
    )
    runner = _runner(scenario)
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    info = excinfo.value.error_info
    assert info.code == "provider_request_rejected"
    assert info.billable is False
    assert info.fallback_eligible is False


def test_run_audio_analysis_service_error_is_retryable(tmp_path: Path) -> None:
    scenario = _Scenario(statuses=[{"status": "ServiceError"}])
    runner = _runner(scenario)
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    info = excinfo.value.error_info
    assert info.code == "provider_unavailable"
    assert info.retryable is True
    assert info.sent is True


def test_run_audio_analysis_deadline_is_timeout(tmp_path: Path) -> None:
    # Always InProgress; the fake monotonic advances past max_wait_s.
    scenario = _Scenario(statuses=[{"status": "InProgress"}] * 500)
    runner = _runner(scenario, settings=_settings(max_wait_s=3.0))
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    info = excinfo.value.error_info
    assert info.code == "provider_unavailable"
    assert info.sent is True


def test_run_audio_analysis_credential_failure_maps_to_auth(tmp_path: Path) -> None:
    scenario = _Scenario(statuses=[])
    bad = _creds(raises=True)
    store = _store(_Scenario(statuses=[]), bad)
    runner = DataAutomationAudioRunner(
        _settings(),
        credentials=bad,
        provider="aws_bedrock",
        store=store,
        transport=httpx.MockTransport(scenario.handler),
        clock=FIXED_CLOCK,
        sleep=lambda _s: None,
        monotonic=_FakeMonotonic(),
    )
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    assert excinfo.value.error_info.code == "provider_auth_failed"


def test_run_audio_analysis_invoke_without_arn_is_malformed(tmp_path: Path) -> None:
    scenario = _Scenario(statuses=[], invoke_payload={})
    runner = _runner(scenario)
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    assert excinfo.value.error_info.code == "provider_malformed_response"


def test_cleanup_disabled_leaves_objects(tmp_path: Path) -> None:
    scenario = _Scenario(
        statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                },
            }
        ],
    )
    scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
        JOB_METADATA
    ).encode()
    scenario._objects["/p/output/tok/0/result.json"] = json.dumps(
        STANDARD_OUTPUT
    ).encode()
    runner = _runner(scenario, settings=_settings(cleanup=False))
    runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    assert scenario.deletes == []


def test_invoke_body_omits_configuration_without_project(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    scenario = _Scenario(
        statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                },
            }
        ],
    )
    scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
        JOB_METADATA
    ).encode()
    scenario._objects["/p/output/tok/0/result.json"] = json.dumps(
        STANDARD_OUTPUT
    ).encode()

    original = scenario._bda

    def capturing(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-amz-target", "").endswith(
            "InvokeDataAutomationAsync"
        ):
            captured.update(json.loads(request.content))
        return original(request)

    scenario._bda = capturing  # type: ignore[assignment]
    runner = _runner(scenario, settings=_settings(project_arn=None))
    runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    assert "dataAutomationConfiguration" not in captured
    assert captured["dataAutomationProfileArn"] == PROFILE_ARN
    assert "clientToken" in captured


def test_invoke_body_includes_configuration_with_project(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    scenario = _Scenario(
        statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                },
            }
        ],
    )
    scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
        JOB_METADATA
    ).encode()
    scenario._objects["/p/output/tok/0/result.json"] = json.dumps(
        STANDARD_OUTPUT
    ).encode()
    original = scenario._bda

    def capturing(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-amz-target", "").endswith(
            "InvokeDataAutomationAsync"
        ):
            captured.update(json.loads(request.content))
        return original(request)

    scenario._bda = capturing  # type: ignore[assignment]
    runner = _runner(scenario)
    runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    config = captured["dataAutomationConfiguration"]
    assert config == {"dataAutomationProjectArn": PROJECT_ARN, "stage": "LIVE"}


def test_missing_standard_output_path_is_malformed(tmp_path: Path) -> None:
    scenario = _Scenario(
        statuses=[
            {
                "status": "Success",
                "outputConfiguration": {
                    "s3Uri": f"s3://{BUCKET}/p/output/tok/job_metadata.json"
                },
            }
        ],
    )
    scenario._objects["/p/output/tok/job_metadata.json"] = json.dumps(
        {"output_metadata": []}
    ).encode()
    runner = _runner(scenario, settings=_settings(cleanup=False))
    with pytest.raises(ProviderCallFailure) as excinfo:
        runner.run_audio_analysis(_audio_file(tmp_path), idempotency_scope="ws:proj")
    assert excinfo.value.error_info.code == "provider_malformed_response"


# --- pure result mappers ---------------------------------------------------
def test_transcription_from_result_builds_words_and_text() -> None:
    out = transcription_from_result(STANDARD_OUTPUT)
    assert out.language == "en"
    assert [w.text for w in out.words] == ["Auto", "sales"]  # invalid item skipped
    assert out.words[0].start_ms == 9
    # Text joins the TRANSCRIPT segments.
    assert "Auto sales, Cherry speaking." in out.text
    assert "How can I help you?" in out.text


def test_transcription_from_result_falls_back_to_representation() -> None:
    result = {
        "metadata": {"dominant_asset_language": "FR"},
        "transcript": {"representation": {"text": "Bonjour le monde"}},
    }
    out = transcription_from_result(result)
    assert out.text == "Bonjour le monde"
    assert out.language == "fr"
    assert out.words == ()


def test_transcription_accepts_nested_audio_key() -> None:
    nested = {
        "audio": {
            "metadata": {"dominant_asset_language": "EN", "duration_millis": 500},
            "audio_items": [
                {
                    "content": "hi",
                    "start_timestamp_millis": 0,
                    "end_timestamp_millis": 100,
                }
            ],
            "audio_segments": [
                {"type": "TRANSCRIPT", "text": "hi there"},
            ],
        }
    }
    out = transcription_from_result(nested)
    assert out.text == "hi there"
    assert out.words[0].text == "hi"
    assert duration_seconds_from_result(nested) == 0.5


def test_diarization_from_result_sorts_segments() -> None:
    out = diarization_from_result(STANDARD_OUTPUT)
    # Sorted by (start_ms, end_ms): spk_0 (0..999) then spk_1 (1000..1970).
    assert [s.speaker for s in out.segments] == ["spk_0", "spk_1"]
    assert out.segments[0].start_ms == 0
    assert out.speaker_count == 2


def test_diarization_without_speaker_labels_raises() -> None:
    no_labels = {
        "audio_segments": [
            {
                "type": "TRANSCRIPT",
                "text": "x",
                "start_timestamp_millis": 0,
                "end_timestamp_millis": 10,
            }
        ]
    }
    with pytest.raises(DataAutomationOutputError):
        diarization_from_result(no_labels)


def test_duration_and_usage_units() -> None:
    assert duration_seconds_from_result(STANDARD_OUTPUT) == pytest.approx(1.97)
    # ceil(1.97) == 2
    assert usage_input_units_from_result(STANDARD_OUTPUT) == 2


def test_duration_falls_back_to_last_timestamp() -> None:
    result = {
        "audio_segments": [
            {
                "type": "TRANSCRIPT",
                "text": "x",
                "speaker": {"speaker_label": "spk_0"},
                "start_timestamp_millis": 0,
                "end_timestamp_millis": 3200,
            }
        ]
    }
    assert duration_seconds_from_result(result) == pytest.approx(3.2)


def test_supported_suffixes_cover_caller_wav() -> None:
    # The transcription/diarization callers stage mono .wav; it must be accepted.
    assert ".wav" in bda.SUPPORTED_AUDIO_SUFFIXES


def test_settings_host_is_region_scoped() -> None:
    assert (
        _settings().host
        == "bedrock-data-automation-runtime.ap-south-1.amazonaws.com"
    )
