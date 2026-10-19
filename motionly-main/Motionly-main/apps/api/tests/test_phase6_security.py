import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx

from app.core.middleware import InMemoryRateLimiter, _verified_identity
from app.services.retention import RetentionService
from app.services.storage import StorageSigner


def test_rate_limit_identity_comes_from_verified_jwt_not_caller_headers() -> None:
    user_id = uuid4()
    token = _jwt({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60})
    request = SimpleNamespace(
        headers={
            "authorization": f"Bearer {token}",
            "x-motionly-workspace-id": str(uuid4()),
            "x-motionly-user-id": str(uuid4()),
        },
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    supabase_jwt_secret="secret",
                    supabase_url=None,
                    supabase_service_role_key=None,
                    app_env="test",
                )
            )
        ),
    )

    assert _verified_identity(request) == f"user:{user_id}"


def test_in_memory_limiter_matches_async_distributed_contract() -> None:
    limiter = InMemoryRateLimiter()
    assert asyncio.run(limiter.allow(key="user:1", limit=1)) == (True, 0)
    assert asyncio.run(limiter.allow(key="user:1", limit=1)) == (False, 0)


def test_download_signatures_are_capped_at_five_minutes(monkeypatch) -> None:
    payloads = []

    def post(*_args, **kwargs):
        payloads.append(kwargs["json"])
        return httpx.Response(200, json={"signedURL": "/object/sign/path"})

    monkeypatch.setattr("app.services.storage.httpx.post", post)
    signer = StorageSigner(
        supabase_url="https://supabase.example",
        service_role_key="service-role",
    )
    signer.create_signed_download(bucket="exports", path="video.mp4", expires_in_seconds=3600)

    assert payloads == [{"expiresIn": 300}]


def test_retention_deletes_storage_before_rows_and_evicts_old_cache(tmp_path) -> None:
    old = tmp_path / "old.cache"
    old.write_bytes(b"cache")
    os.utime(old, (1, 1))
    calls = []
    signer = SimpleNamespace(
        delete_object=lambda **kwargs: calls.append(("storage", kwargs["path"]))
    )
    settings = SimpleNamespace(
        supabase_url="https://supabase.example",
        supabase_service_role_key="service-role",
        retention_export_days=30,
        retention_source_days=365,
        retention_transcript_days=365,
        retention_analytics_days=365,
        retention_generated_asset_days=90,
        retention_local_cache_days=7,
        media_work_dir=str(tmp_path),
    )
    service = RetentionService(settings=settings, storage_signer=signer)

    def request(method, table, **_kwargs):
        if method == "GET" and table == "exports":
            return [{"id": "export-1", "storage_bucket": "exports", "storage_path": "x.mp4"}]
        if method == "DELETE":
            calls.append(("row", table))
        return []

    service._request = request
    counts = service.run()

    assert calls[:2] == [("storage", "x.mp4"), ("row", "exports")]
    assert counts["exports"] == 1
    assert counts["local_cache_files"] == 1
    assert not old.exists()


def _jwt(claims: dict[str, object]) -> str:
    encoded_header = _encode({"alg": "HS256", "typ": "JWT"})
    encoded_claims = _encode(claims)
    signature = hmac.new(
        b"secret", f"{encoded_header}.{encoded_claims}".encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded_header}.{encoded_claims}.{encoded_signature}"


def _encode(value: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
