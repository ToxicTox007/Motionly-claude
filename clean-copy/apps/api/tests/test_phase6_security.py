import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.core.middleware import InMemoryRateLimiter, _verified_identity
from app.repositories.publishing import InMemoryPublishingRepository, SupabasePublishingRepository
from app.schemas.publishing import PublishConnectionStatus, PublishPlatform, PublishStatus
from app.services.publishing import (
    OAuthAccount,
    PublishedPost,
    SocialPublishingClient,
    execute_publish_job,
)
from app.services.retention import RetentionService
from app.services.storage import StorageSigner
from app.services.token_cipher import TokenCipher


def test_social_tokens_use_authenticated_encryption_and_detect_tampering() -> None:
    cipher = TokenCipher("a-production-grade-key-that-is-long-enough")
    encrypted = cipher.encrypt("provider-secret")

    assert encrypted and encrypted.startswith("enc:v1:")
    assert "provider-secret" not in encrypted
    assert cipher.decrypt(encrypted) == "provider-secret"

    with pytest.raises(ApiError):
        cipher.decrypt(encrypted[:-2] + "xx")


def test_supabase_repository_never_writes_plain_social_tokens(monkeypatch) -> None:
    cipher = TokenCipher("a-production-grade-key-that-is-long-enough")
    written = {}

    def request(_method, _url, *, json, **_kwargs):
        written.update(json)
        return httpx.Response(
            201,
            json=[
                {
                    "id": str(uuid4()),
                    "workspace_id": json["workspace_id"],
                    "platform": json["platform"],
                    "access_token": json["access_token"],
                    "refresh_token": json["refresh_token"],
                    "token_expires_at": None,
                    "external_account_id": json["external_account_id"],
                    "account_name": json["account_name"],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ],
        )

    monkeypatch.setattr("app.repositories.publishing.httpx.request", request)
    repository = SupabasePublishingRepository(
        supabase_url="https://supabase.example",
        service_role_key="service-role",
        token_cipher=cipher,
    )
    connection = repository.create_connection(
        workspace_id=uuid4(),
        platform=PublishPlatform.YOUTUBE,
        access_token="plain-access-token",
        refresh_token="plain-refresh-token",
        token_expires_at=None,
        external_account_id="channel",
        account_name="Channel",
    )

    assert written["access_token"].startswith("enc:v1:")
    assert written["refresh_token"].startswith("enc:v1:")
    assert "plain" not in written["access_token"]
    assert connection.access_token == "plain-access-token"


def test_expiring_connection_is_refreshed_before_publish() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="expired-access-token",
        refresh_token="refresh-token",
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        external_account_id="channel",
        account_name="Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Title",
        caption="",
        privacy="private",
    )

    oauth = SimpleNamespace(
        refresh=lambda _connection: OAuthAccount(
            external_account_id="channel",
            account_name="Channel",
            access_token="fresh-access-token",
            refresh_token="rotated-refresh-token",
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            refresh_token_expires_at=datetime.now(UTC) + timedelta(days=30),
            scopes=["youtube.upload"],
        )
    )

    class Client:
        def publish(self, *, connection, **_kwargs):
            assert connection.access_token == "fresh-access-token"
            return PublishedPost(external_post_id="post-1")

    execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(
                storage_bucket="exports", storage_path="video.mp4"
            )
        ),
        storage_signer=SimpleNamespace(
            create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
        ),
        client=Client(),
        oauth_client=oauth,
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert connection.refresh_token == "rotated-refresh-token"
    assert connection.connection_status == PublishConnectionStatus.ACTIVE
    assert connection.last_refresh_at is not None
    assert connection.scopes == ["youtube.upload"]


def test_publish_refresh_requires_reconnect_when_refresh_token_missing() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="expired-access-token",
        refresh_token=None,
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        external_account_id="channel",
        account_name="Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Title",
        caption="",
        privacy="private",
    )

    execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(
                storage_bucket="exports", storage_path="video.mp4"
            )
        ),
        storage_signer=SimpleNamespace(
            create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
        ),
        client=SimpleNamespace(publish=lambda **_kwargs: None),
        oauth_client=SimpleNamespace(
            refresh=lambda _connection: (_ for _ in ()).throw(
                ApiError(
                    401,
                    "publish_reauthorization_required",
                    "YouTube authorization expired. Reconnect the account.",
                    details={"reconnect_required": True, "error_code": "revoked_token"},
                )
            )
        ),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    updated_connection = repository.get_connection(
        connection_id=connection.id, workspace_id=workspace_id
    )
    updated_job = repository.get_job(job_id=job.id, workspace_id=workspace_id)
    assert updated_connection.connection_status == PublishConnectionStatus.RECONNECT_REQUIRED
    assert updated_job.status == PublishStatus.RECONNECT_REQUIRED
    assert updated_job.reconnect_required is True


def test_valid_non_expiring_token_publishes_without_unnecessary_refresh() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="valid-access-token",
        refresh_token="refresh-token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=2),
        external_account_id="channel",
        account_name="Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Title",
        caption="",
        privacy="private",
    )
    refresh_calls = []

    class Client:
        def publish(self, *, connection, **_kwargs):
            assert connection.access_token == "valid-access-token"
            return PublishedPost(external_post_id="post-1")

    execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(
                storage_bucket="exports", storage_path="video.mp4"
            )
        ),
        storage_signer=SimpleNamespace(
            create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
        ),
        client=Client(),
        oauth_client=SimpleNamespace(
            refresh=lambda _connection: refresh_calls.append("refresh")
        ),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert refresh_calls == []


def test_refresh_failure_is_logged_safely(caplog) -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.TIKTOK,
        access_token="top-secret-access-token",
        refresh_token="top-secret-refresh-token",
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        external_account_id="creator",
        account_name="Creator",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Title",
        caption="",
        privacy="private",
    )
    caplog.set_level(logging.WARNING)

    execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(
                storage_bucket="exports", storage_path="video.mp4"
            )
        ),
        storage_signer=SimpleNamespace(
            create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
        ),
        client=SimpleNamespace(publish=lambda **_kwargs: None),
        oauth_client=SimpleNamespace(
            refresh=lambda _connection: (_ for _ in ()).throw(
                ApiError(
                    401,
                    "publish_reauthorization_required",
                    "TikTok authorization expired. Reconnect the account.",
                    details={"reconnect_required": True, "error_code": "revoked_token"},
                )
            )
        ),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "top-secret-access-token" not in messages
    assert "top-secret-refresh-token" not in messages


def test_publish_retries_once_after_provider_401_and_persists_rotated_tokens() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="old-token",
        refresh_token="refresh-token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        external_account_id="channel",
        account_name="Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Title",
        caption="",
        privacy="private",
    )
    calls = []

    class Client:
        def publish(self, *, connection, **_kwargs):
            calls.append(connection.access_token)
            if len(calls) == 1:
                raise ApiError(
                    401,
                    "publish_token_expired",
                    "Publishing authorization expired. Refreshing the connection is required.",
                    details={"error_code": "publish_token_expired"},
                )
            return PublishedPost(external_post_id="post-1")

    execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(
                storage_bucket="exports", storage_path="video.mp4"
            )
        ),
        storage_signer=SimpleNamespace(
            create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
        ),
        client=Client(),
        oauth_client=SimpleNamespace(
            refresh=lambda _connection: OAuthAccount(
                external_account_id="channel",
                account_name="Channel",
                access_token="fresh-token",
                refresh_token="rotated-refresh-token",
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        ),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    updated_connection = repository.get_connection(
        connection_id=connection.id, workspace_id=workspace_id
    )
    updated_job = repository.get_job(job_id=job.id, workspace_id=workspace_id)
    assert calls == ["old-token", "fresh-token"]
    assert updated_connection.refresh_token == "rotated-refresh-token"
    assert updated_job.status == PublishStatus.PUBLISHED


def test_youtube_upload_streams_source_into_resumable_session(monkeypatch) -> None:
    consumed = []

    class Media:
        headers = {"content-type": "video/mp4", "content-length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 1024 * 1024
            yield b"abc"
            yield b"def"

        @property
        def content(self):
            raise AssertionError("streaming path must not buffer response.content")

    monkeypatch.setattr(
        "app.services.publishing.httpx.post",
        lambda *_args, **_kwargs: httpx.Response(
            200, headers={"location": "https://upload.youtube/session"}
        ),
    )
    monkeypatch.setattr("app.services.publishing.httpx.stream", lambda *_a, **_k: Media())

    def put(_url, *, content, **_kwargs):
        consumed.extend(content)
        return httpx.Response(200, json={"id": "youtube-id"})

    monkeypatch.setattr("app.services.publishing.httpx.put", put)
    connection = SimpleNamespace(
        platform=PublishPlatform.YOUTUBE,
        access_token="access-token",
        external_account_id="channel",
    )
    job = SimpleNamespace(title="Title", caption="Caption", privacy="private")

    result = SocialPublishingClient()._youtube(
        connection=connection, job=job, video_url="https://signed/source.mp4"
    )

    assert consumed == [b"abc", b"def"]
    assert result.external_post_id == "youtube-id"


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
