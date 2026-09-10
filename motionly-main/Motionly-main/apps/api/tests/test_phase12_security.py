import logging
from pathlib import Path
from types import SimpleNamespace

from app.core.logging import RedactingJsonFormatter, redact_data, redact_string
from app.services.retention import RetentionService


def test_redact_string_bearer_tokens() -> None:
    text = "Authorization: Bearer gsk_abc123xyz456"
    assert redact_string(text) == "Authorization: Bearer [REDACTED]"

    text_lower = "authorization: bearer sk-or-v1-abc"
    assert redact_string(text_lower) == "authorization: bearer [REDACTED]"


def test_redact_string_signed_urls() -> None:
    url = "https://supabase.co/bucket/file.mp4?token=secret123&expires=1780000000&signature=sig456"
    redacted = redact_string(url)
    assert "token=[REDACTED]" in redacted
    assert "expires=[REDACTED]" in redacted
    assert "signature=[REDACTED]" in redacted


def test_redact_data_nested() -> None:
    data = {
        "api_key": "gsk_123",
        "nested": {
            "token": "sk-or-v1-456",
            "secret": "rwy_789",
            "access_token": "some-token",
            "safe_field": "hello",
        },
        "list": [{"password": "pw", "other": "world"}],
    }
    redacted = redact_data(data)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["secret"] == "[REDACTED]"
    assert redacted["nested"]["access_token"] == "[REDACTED]"
    assert redacted["nested"]["safe_field"] == "hello"
    assert redacted["list"][0]["password"] == "[REDACTED]"
    assert redacted["list"][0]["other"] == "world"


def test_redacting_formatter() -> None:
    formatter = RedactingJsonFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="API call failed with Bearer sk-or-v1-abc123xyz",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "sk-or-v1-abc123xyz" not in formatted
    assert "Bearer [REDACTED]" in formatted


def test_provider_failures_redacted() -> None:
    # Test Groq whisper exception scrubbing simulated
    try:
        raise ValueError("Failed connecting to Groq with Bearer gsk_abc123")
    except Exception as exc:
        err_msg = redact_string(str(exc))
        assert "gsk_abc123" not in err_msg
        assert "Bearer [REDACTED]" in err_msg


def test_worker_exception_redacted() -> None:
    # Test workers response exception serialization mock
    try:
        raise ValueError("DB error with signature=sig123&token=tok456")
    except Exception as exc:
        err_msg = redact_string(str(exc))
        assert "sig123" not in err_msg
        assert "tok456" not in err_msg
        assert "signature=[REDACTED]" in err_msg
        assert "token=[REDACTED]" in err_msg


def test_retention_keeps_assets_expires_exports(tmp_path) -> None:
    calls = []
    signer = SimpleNamespace(
        delete_object=lambda **kwargs: calls.append(("storage", kwargs["path"]))
    )
    settings = SimpleNamespace(
        supabase_url="https://supabase.example",
        supabase_service_role_key="service-role",
        retention_export_days=90,
        retention_local_cache_days=7,
        media_work_dir=str(tmp_path),
    )
    service = RetentionService(settings=settings, storage_signer=signer)

    def request(method, table, **kwargs):
        if method == "GET" and table == "exports":
            return [{"id": "export-1", "storage_bucket": "exports", "storage_path": "x.mp4"}]
        if method == "DELETE":
            calls.append(("row", table, kwargs.get("params", {}).get("id")))
        if method == "PATCH" and table == "transcripts":
            calls.append(("patch", table, kwargs.get("json", {})))
        return []

    service._request = request
    counts = service.run()

    # Exports should be deleted
    assert counts["exports"] == 1
    assert ("storage", "x.mp4") in calls
    assert ("row", "exports", "eq.export-1") in calls

    # Transcripts payloads should be patched/redacted
    assert any(
        c[0] == "patch" and c[1] == "transcripts" and c[2] == {"raw_payload": {}} for c in calls
    )

    # Database sources, transcripts, and generated assets are not deleted by age
    # in the Python retention service.
    assert not any(
        c[0] == "row"
        and c[1] in ("uploaded_videos", "transcripts", "phase5_resources", "stock_assets")
        for c in calls
    )


def test_retention_deletion_queue_filtering() -> None:
    calls = []
    signer = SimpleNamespace(
        delete_object=lambda **kwargs: calls.append(("storage", kwargs["path"]))
    )
    settings = SimpleNamespace(
        supabase_url="https://supabase.example",
        supabase_service_role_key="service-role",
        retention_export_days=90,
        media_work_dir="/tmp",
    )
    service = RetentionService(settings=settings, storage_signer=signer)

    def request(method, table, **kwargs):
        if method == "POST" and table == "rpc/motionly_claim_storage_deletion_queue":
            payload = kwargs["json"]
            assert payload["p_lease_owner"].startswith("retention:")
            assert payload["p_before"] is not None
            return [
                {
                    "id": "q-1",
                    "storage_bucket": "raw-uploads",
                    "storage_path": "v1.mp4",
                    "lease_token": "lease-1",
                }
            ]
        if method == "POST" and table == "rpc/motionly_ack_storage_deletion_queue":
            calls.append(("ack_queue_row", kwargs["json"]))
            return True
        return []

    service._request = request
    counts = service._drain_storage_deletion_queue()
    assert counts == 1
    assert ("storage", "v1.mp4") in calls
    assert ("ack_queue_row", {"p_queue_id": "q-1", "p_lease_token": "lease-1"}) in calls


def test_data_handling_documentation_exists() -> None:
    doc_path = Path(__file__).resolve().parents[3] / "docs" / "security" / "data-handling.md"
    assert doc_path.exists()
    content = doc_path.read_text()
    assert "Groq" in content
    assert "OpenRouter" in content
    assert "Runway" in content
    assert "Pexels" in content or "Stock Providers" in content
    assert "Internal Storage" in content
