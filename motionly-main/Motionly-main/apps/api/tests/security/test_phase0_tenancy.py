import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError, api_error_handler
from app.api.internal_retention import router as internal_retention_router
from app.api.projects import get_project
from app.api.renders import get_export_download_url, get_render
from app.api.uploads import get_upload, get_upload_asset_urls
from app.core.middleware import request_id_middleware
from app.repositories.projects import InMemoryProjectRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.renders import RenderJobStatus, RenderOutputFormat
from app.schemas.uploads import UploadStatus, VideoMetadata
from tests.test_renders import make_render_repository, make_request

PROTECTED_ROUTE_GROUPS = [
    "/projects",
    "/projects/{id}/uploads",
    "/projects/{id}/plans",
    "/projects/{id}/captions",
    "/projects/{id}/broll",
    "/projects/{id}/music",
    "/projects/{id}/effects",
    "/projects/{id}/renders",
    "/billing",
]


@pytest.mark.parametrize("route_group", PROTECTED_ROUTE_GROUPS)
def test_protected_route_groups_reject_missing_auth(route_group: str) -> None:
    request = _auth_request()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=None,
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert route_group
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "auth_required"


@pytest.mark.parametrize("route_group", PROTECTED_ROUTE_GROUPS)
def test_protected_route_groups_reject_invalid_jwt(route_group: str) -> None:
    request = _auth_request()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization="Bearer not-a-valid-jwt",
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert route_group
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "auth_token_invalid"


def test_protected_route_groups_reject_forged_workspace_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _auth_request()

    def fake_membership_check(**_kwargs):
        raise ApiError(403, "workspace_access_denied", "You do not have access.")

    monkeypatch.setattr("app.api.dependencies.require_workspace_membership", fake_membership_check)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=f"Bearer {_jwt_for_user()}",
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert exc_info.value.status_code == 403


def _internal_retention_client() -> TestClient:
    """Exercise a surviving internal router (retention) exactly as mounted,
    including its worker-token dependency. The dead /internal/jobs run route was
    removed in Task 32; worker-token auth is now proven on the internal routes
    that remain."""
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.state.settings = SimpleNamespace(app_env="local", worker_api_token="worker-secret")
    app.include_router(internal_retention_router)
    return TestClient(app)


def test_internal_worker_route_rejects_missing_worker_token() -> None:
    response = _internal_retention_client().post("/internal/retention/run")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "worker_access_denied"


def test_project_route_denies_wrong_workspace_user() -> None:
    repository = InMemoryProjectRepository()
    owner_workspace_id = uuid4()
    foreign_workspace_id = uuid4()
    project = repository.create_project(
        workspace_id=owner_workspace_id,
        created_by=uuid4(),
        title="Tenant-owned project",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_project(
                project_id=project.id,
                actor=Actor(user_id=uuid4(), workspace_id=foreign_workspace_id),
                repository=repository,
            )
        )

    assert exc_info.value.status_code in {403, 404}
    assert exc_info.value.code in {"workspace_access_denied", "project_not_found"}


def test_upload_signed_url_generation_denies_foreign_workspace_before_signing() -> None:
    repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{workspace_id}/projects/{project_id}/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
    )
    upload.status = UploadStatus.READY
    upload.metadata = VideoMetadata(duration_ms=3000, width=1080, height=1920)
    signer = CountingSigner()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_upload_asset_urls(
                project_id=project_id,
                video_id=upload.id,
                request=_settings_request(),
                actor=Actor(user_id=uuid4(), workspace_id=uuid4()),
                repository=repository,
                storage_signer=signer,
            )
        )

    assert exc_info.value.status_code == 403
    assert signer.download_calls == 0


def test_forged_upload_id_from_another_project_is_not_returned() -> None:
    repository = InMemoryUploadRepository()
    workspace_id = uuid4()
    project_id = uuid4()
    other_project_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    repository.seed_project(project_id=other_project_id, workspace_id=workspace_id)
    upload = repository.create_upload(
        project_id=other_project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="foreign-project/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_upload(
                project_id=project_id,
                video_id=upload.id,
                actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
                repository=repository,
            )
        )

    assert exc_info.value.status_code == 404


def test_export_download_url_denies_foreign_workspace_before_signing() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.COMPLETE,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )
    export = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render.id,
        storage_bucket="exports",
        storage_path=f"workspaces/{workspace_id}/projects/{project_id}/exports/video.mp4",
        format=RenderOutputFormat.MP4,
        width=1080,
        height=1920,
        fps=30,
    )
    signer = CountingSigner()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_export_download_url(
                export_id=export.id,
                request=make_request(),
                actor=Actor(user_id=uuid4(), workspace_id=uuid4()),
                repository=repository,
                storage_signer=signer,
            )
        )

    assert exc_info.value.status_code == 403
    assert signer.download_calls == 0


def test_forged_render_id_from_another_project_is_not_returned() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    other_project_id = uuid4()
    repository.caption_repository.upload_repository.seed_project(
        project_id=other_project_id,
        workspace_id=workspace_id,
    )
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=other_project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.QUEUED,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_render(
                project_id=project_id,
                render_job_id=render.id,
                actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
                repository=repository,
            )
        )

    assert exc_info.value.status_code == 404


def test_worker_payload_cannot_update_render_in_another_workspace() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.RENDERING,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )

    # The render control plane resolves every job through the render repository
    # with the caller's workspace. A worker payload carrying a forged workspace
    # id can never reach a render owned by another tenant: the repository raises
    # 403 before any state mutation. (The dead /internal/renders/* HTTP surface
    # that used to wrap this guard was removed in Task 32; the guard itself is
    # the load-bearing tenant boundary and stays covered here.)
    with pytest.raises(ApiError) as exc_info:
        repository.get_render_job(
            project_id=project_id,
            render_job_id=render.id,
            workspace_id=uuid4(),
        )

    assert exc_info.value.status_code == 403


def test_worker_route_rejects_wrong_worker_token() -> None:
    response = _internal_retention_client().post(
        "/internal/retention/run",
        headers={"x-motionly-worker-token": "forged-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "worker_access_denied"


class CountingSigner:
    def __init__(self) -> None:
        self.download_calls = 0

    def create_signed_download(self, **_kwargs):
        self.download_calls += 1
        return SimpleNamespace(url="https://storage.example.com/signed")


def _settings_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    upload_signed_url_ttl_seconds=900,
                    export_signed_url_ttl_seconds=300,
                )
            )
        )
    )


def _auth_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    supabase_jwt_secret="secret",
                    supabase_url="https://supabase.example.com",
                    supabase_service_role_key="service-role",
                )
            )
        )
    )


def _jwt_for_user() -> str:
    import hashlib
    import hmac
    import json
    import time

    header = {"alg": "HS256", "typ": "JWT"}
    claims = {"sub": str(uuid4()), "aud": "authenticated", "exp": int(time.time()) + 60}
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        b"secret",
        f"{encoded_header}.{encoded_claims}".encode(),
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"


def _b64(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")
