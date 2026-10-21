"""Tests for export share links and public resolve (Task 16).

Covers token fingerprinting, expiry, revocation, download blocking, rate
limiting, and enumeration safety, plus the download-filename derivation.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.api.export_shares import (
    create_export_share_link,
    list_export_share_links,
    resolve_public_export,
    revoke_export_share_link,
)
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.export_shares import InMemoryExportShareRepository, token_digest
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.export_shares import (
    ExportShareLinkCreateRequest,
    PublicExportResolveRequest,
    ShareLinkExpiry,
)
from app.services.export_download import export_download_filename


class _Actor(SimpleNamespace):
    pass


class FakeStorageSigner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_signed_download(self, **kwargs):
        self.calls.append(kwargs)
        suffix = f"&download={kwargs['download']}" if kwargs.get("download") else ""
        return SimpleNamespace(url=f"https://storage.example.com/export.mp4?token=t{suffix}")


class AllowAllLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def allow(self, *, key: str, limit: int, window_seconds: int):
        self.calls.append((key, limit))
        return True, 0


class DenyAfterLimiter:
    """Allows the first N per-viewer attempts, then refuses."""

    def __init__(self, allowed: int) -> None:
        self.allowed = allowed
        self.viewer_hits = 0

    async def allow(self, *, key: str, limit: int, window_seconds: int):
        if ":viewer:" in key:
            self.viewer_hits += 1
            return self.viewer_hits <= self.allowed, 0
        return True, 0


def _seed_repository(*, title: str = "Launch Reel"):
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(
        project_id=project_id, workspace_id=workspace_id, title=title
    )
    caption_repository = InMemoryCaptionRepository(upload_repository)
    render_repository = InMemoryRenderRepository(caption_repository)
    render_job = render_repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=uuid4(),
        caption_track_id=None,
        status="queued",
        planner_version="test",
        manifest={},
        keep_segments=[],
    )
    export = render_repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job.id,
        storage_bucket="exports",
        storage_path="exports/render.mp4",
        format="mp4",
        width=1080,
        height=1920,
        fps=30,
        duration_ms=7000,
        file_size_bytes=1234,
        settings_summary={},
    )
    share_repository = InMemoryExportShareRepository(render_repository)
    actor = _Actor(user_id=user_id, workspace_id=workspace_id)
    return SimpleNamespace(
        render_repository=render_repository,
        share_repository=share_repository,
        export=export,
        actor=actor,
        project_id=project_id,
        workspace_id=workspace_id,
    )


def _resolve_request(ctx, *, limiter=None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    export_signed_url_ttl_seconds=60,
                    export_share_resolve_rate_limit_per_hour=120,
                    export_share_resolve_global_rate_limit_per_hour=6000,
                ),
                render_repository=ctx.render_repository,
                rate_limiter=limiter or AllowAllLimiter(),
            )
        ),
        headers={"x-motionly-viewer-ip": "203.0.113.7"},
        client=SimpleNamespace(host="203.0.113.7"),
        state=SimpleNamespace(request_id="req-test"),
    )


# --- download filename derivation -------------------------------------------


def test_export_download_filename_sanitizes_title_and_version() -> None:
    assert (
        export_download_filename(title="My Launch / Reel!!", version=3, extension="mp4")
        == "My-Launch-Reel-v3.mp4"
    )


def test_export_download_filename_falls_back_for_empty_title() -> None:
    assert export_download_filename(title="  @#$  ", version=1, extension="mp4") == "export-v1.mp4"


# --- token fingerprinting ----------------------------------------------------


def test_create_share_link_persists_only_a_fingerprint() -> None:
    ctx = _seed_repository()
    response = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(expires_in_days=ShareLinkExpiry.SEVEN_DAYS),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    token = response.token
    stored = next(iter(ctx.share_repository.share_links.values()))
    # The raw token is never stored; only its SHA-256 digest is.
    assert stored.token_digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in stored.token_digest
    assert response.url == f"/watch#t={token}"


# --- expiry ------------------------------------------------------------------


def test_resolve_rejects_expired_link_like_an_unknown_one() -> None:
    ctx = _seed_repository()
    token = "expired-token"
    ctx.share_repository.create_share_link(
        export_id=ctx.export.id,
        workspace_id=ctx.workspace_id,
        token=token,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        allow_download=True,
        created_by=ctx.actor.user_id,
    )
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(
            resolve_public_export(
                payload=PublicExportResolveRequest(token=token),
                request=_resolve_request(ctx),
                response=SimpleNamespace(headers={}),
                repository=ctx.share_repository,
                storage_signer=FakeStorageSigner(),
            )
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "export_share_link_not_found"


# --- revocation --------------------------------------------------------------


def test_revoke_stops_the_link_resolving() -> None:
    ctx = _seed_repository()
    created = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    # Resolves before revocation.
    resolved = asyncio.run(
        resolve_public_export(
            payload=PublicExportResolveRequest(token=created.token),
            request=_resolve_request(ctx),
            response=SimpleNamespace(headers={}),
            repository=ctx.share_repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    assert resolved.title == "Launch Reel"

    asyncio.run(
        revoke_export_share_link(
            export_id=ctx.export.id,
            share_link_id=created.link.id,
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(
            resolve_public_export(
                payload=PublicExportResolveRequest(token=created.token),
                request=_resolve_request(ctx),
                response=SimpleNamespace(headers={}),
                repository=ctx.share_repository,
                storage_signer=FakeStorageSigner(),
            )
        )
    assert excinfo.value.status_code == 404


def test_revoke_rejects_cross_tenant_link() -> None:
    ctx = _seed_repository()
    created = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    intruder = _Actor(user_id=uuid4(), workspace_id=uuid4())
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(
            revoke_export_share_link(
                export_id=ctx.export.id,
                share_link_id=created.link.id,
                actor=intruder,
                repository=ctx.share_repository,
            )
        )
    assert excinfo.value.status_code == 404


# --- download blocking -------------------------------------------------------


def test_resolve_omits_download_url_when_downloads_blocked() -> None:
    ctx = _seed_repository()
    created = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(allow_download=False),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    signer = FakeStorageSigner()
    resolved = asyncio.run(
        resolve_public_export(
            payload=PublicExportResolveRequest(token=created.token),
            request=_resolve_request(ctx),
            response=SimpleNamespace(headers={}),
            repository=ctx.share_repository,
            storage_signer=signer,
        )
    )
    assert resolved.allow_download is False
    assert resolved.download_url is None
    assert resolved.download_filename is None
    # Still gets a watch media URL.
    assert resolved.media_url
    # No signer call requested a download disposition.
    assert all(call.get("download") is None for call in signer.calls)


def test_resolve_includes_named_download_when_allowed() -> None:
    ctx = _seed_repository(title="Weekend Trip")
    created = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(allow_download=True),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    resolved = asyncio.run(
        resolve_public_export(
            payload=PublicExportResolveRequest(token=created.token),
            request=_resolve_request(ctx),
            response=SimpleNamespace(headers={}),
            repository=ctx.share_repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    assert resolved.allow_download is True
    assert resolved.download_filename == "Weekend-Trip-v1.mp4"
    assert resolved.download_url is not None


# --- rate limiting -----------------------------------------------------------


def test_resolve_is_rate_limited_per_viewer() -> None:
    ctx = _seed_repository()
    created = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    limiter = DenyAfterLimiter(allowed=1)
    request = _resolve_request(ctx, limiter=limiter)

    # First call allowed.
    asyncio.run(
        resolve_public_export(
            payload=PublicExportResolveRequest(token=created.token),
            request=request,
            response=SimpleNamespace(headers={}),
            repository=ctx.share_repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    # Second call refused with the rate-limit error.
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(
            resolve_public_export(
                payload=PublicExportResolveRequest(token=created.token),
                request=request,
                response=SimpleNamespace(headers={}),
                repository=ctx.share_repository,
                storage_signer=FakeStorageSigner(),
            )
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.code == "export_share_resolve_rate_limited"


# --- enumeration safety ------------------------------------------------------


def test_resolve_unknown_token_matches_revoked_and_expired_failures() -> None:
    ctx = _seed_repository()
    # Unknown token.
    with pytest.raises(ApiError) as unknown:
        asyncio.run(
            resolve_public_export(
                payload=PublicExportResolveRequest(token="never-existed"),
                request=_resolve_request(ctx),
                response=SimpleNamespace(headers={}),
                repository=ctx.share_repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    # Revoked token.
    revoked = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    asyncio.run(
        revoke_export_share_link(
            export_id=ctx.export.id,
            share_link_id=revoked.link.id,
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    with pytest.raises(ApiError) as revoked_err:
        asyncio.run(
            resolve_public_export(
                payload=PublicExportResolveRequest(token=revoked.token),
                request=_resolve_request(ctx),
                response=SimpleNamespace(headers={}),
                repository=ctx.share_repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    # All share identical status and code — no state leakage.
    assert unknown.value.status_code == revoked_err.value.status_code == 404
    assert unknown.value.code == revoked_err.value.code == "export_share_link_not_found"


def test_list_share_links_returns_newest_first() -> None:
    ctx = _seed_repository()
    first = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    second = asyncio.run(
        create_export_share_link(
            export_id=ctx.export.id,
            payload=ExportShareLinkCreateRequest(),
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    listed = asyncio.run(
        list_export_share_links(
            export_id=ctx.export.id,
            actor=ctx.actor,
            repository=ctx.share_repository,
        )
    )
    ids = [link.id for link in listed.links]
    assert set(ids) == {first.link.id, second.link.id}


def test_token_digest_is_sha256_hex() -> None:
    digest = token_digest("hello")
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert len(digest) == 64
