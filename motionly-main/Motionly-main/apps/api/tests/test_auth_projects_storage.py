import asyncio
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import get_actor
from app.api.errors import ApiError
from app.api.projects import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
)
from app.repositories.projects import InMemoryProjectRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.projects import ProjectCreateRequest, ProjectStatus, ProjectUpdateRequest
from app.services.auth import (
    VerifiedToken,
    clear_membership_cache_for_test,
    require_workspace_membership,
)
from app.services.operations import MetricsRegistry


def test_get_actor_verifies_supabase_jwt_and_ignores_spoofed_user_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    token = _jwt({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60})
    request = _request(
        app_env="test",
        supabase_jwt_secret="secret",
        supabase_url="https://supabase.example.com",
        supabase_service_role_key="service-role",
    )

    def fake_membership_check(**kwargs):
        assert kwargs["user_id"] == user_id
        assert kwargs["workspace_id"] == workspace_id

    monkeypatch.setattr("app.api.dependencies.require_workspace_membership", fake_membership_check)

    actor = asyncio.run(
        get_actor(
            request=request,
            authorization=f"Bearer {token}",
            x_motionly_workspace_id=str(workspace_id),
        )
    )

    assert actor.user_id == user_id
    assert actor.workspace_id == workspace_id


def test_get_actor_rejects_invalid_token() -> None:
    request = _request(app_env="test", supabase_jwt_secret="secret")

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization="Bearer not-a-jwt",
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "auth_token_invalid"


def test_get_actor_rejects_forged_user_header_without_bearer_token() -> None:
    request = _request(app_env="test", supabase_jwt_secret="secret")

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=None,
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "auth_required"


def test_get_actor_rejects_forged_workspace_header_without_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    forged_workspace_id = uuid4()
    token = _jwt({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60})
    request = _request(
        app_env="production",
        supabase_jwt_secret="secret",
        supabase_url="https://supabase.example.com",
        supabase_service_role_key="service-role",
    )

    def fake_membership_check(**kwargs):
        assert kwargs["user_id"] == user_id
        assert kwargs["workspace_id"] == forged_workspace_id
        raise ApiError(403, "workspace_access_denied", "You do not have access.")

    monkeypatch.setattr("app.api.dependencies.require_workspace_membership", fake_membership_check)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=f"Bearer {token}",
                x_motionly_workspace_id=str(forged_workspace_id),
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "workspace_access_denied"


def test_get_actor_falls_back_to_supabase_auth_for_asymmetric_tokens(monkeypatch) -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    token = _jwt(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60},
        algorithm="RS256",
    )
    request = _request(
        app_env="production",
        supabase_jwt_secret="legacy-secret",
        supabase_url="https://supabase.example.com",
        supabase_service_role_key="service-role",
    )

    def fake_verify_with_auth_api(**_kwargs):
        return VerifiedToken(user_id=user_id, claims={})

    def fake_membership_check(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.api.dependencies.verify_supabase_token_with_auth_api",
        fake_verify_with_auth_api,
    )
    monkeypatch.setattr("app.api.dependencies.require_workspace_membership", fake_membership_check)

    actor = asyncio.run(
        get_actor(
            request=request,
            authorization=f"Bearer {token}",
            x_motionly_workspace_id=str(workspace_id),
        )
    )

    assert actor.user_id == user_id
    assert actor.workspace_id == workspace_id


def test_get_actor_reuses_token_verified_by_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    request = _request(
        app_env="production",
        supabase_jwt_secret="secret",
        supabase_url="https://supabase.example.com",
        supabase_service_role_key="service-role",
    )
    request.state.verified_supabase_token = VerifiedToken(user_id=user_id, claims={})

    def fail_if_called(**_kwargs):
        raise AssertionError("JWT verification should be reused from request.state")

    def fake_membership_check(**kwargs):
        assert kwargs["user_id"] == user_id
        assert kwargs["workspace_id"] == workspace_id

    monkeypatch.setattr("app.api.dependencies.verify_supabase_jwt", fail_if_called)
    monkeypatch.setattr("app.api.dependencies.require_workspace_membership", fake_membership_check)

    actor = asyncio.run(
        get_actor(
            request=request,
            authorization="Bearer already-verified",
            x_motionly_workspace_id=str(workspace_id),
        )
    )

    assert actor.user_id == user_id
    assert actor.workspace_id == workspace_id


def test_workspace_membership_check_caches_success(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_membership_cache_for_test()


def test_workspace_membership_check_records_cache_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_membership_cache_for_test()
    monkeypatch.setenv("AUTH_MEMBERSHIP_CACHE_TTL_SECONDS", "60")
    metrics = MetricsRegistry()

    def fake_get(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: [{"workspace_id": "ok"}])

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)
    user_id = uuid4()
    workspace_id = uuid4()

    require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
        metrics_registry=metrics,
    )
    require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
        metrics_registry=metrics,
    )

    output = metrics.render()
    assert 'motionly_auth_membership_cache_count_count{result="miss"} 1' in output
    assert 'motionly_auth_membership_cache_count_count{result="hit"} 1' in output
    clear_membership_cache_for_test()
    monkeypatch.setenv("AUTH_MEMBERSHIP_CACHE_TTL_SECONDS", "60")
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [{"workspace_id": "ok"}])

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)
    user_id = uuid4()
    workspace_id = uuid4()

    require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )

    assert len(calls) == 1
    clear_membership_cache_for_test()


def test_get_actor_fails_closed_in_production_without_jwt_secret() -> None:
    request = _request(app_env="production", supabase_jwt_secret=None)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=None,
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "auth_not_configured"


def test_get_actor_fails_closed_in_staging_without_jwt_secret() -> None:
    request = _request(app_env="staging", supabase_jwt_secret=None)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            get_actor(
                request=request,
                authorization=None,
                x_motionly_workspace_id=str(uuid4()),
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "auth_not_configured"


def test_mock_bearer_token_is_not_a_jwt_bypass() -> None:
    from app.services.auth import verify_supabase_jwt

    with pytest.raises(ApiError) as exc_info:
        verify_supabase_jwt(token="mock-local-bearer", jwt_secret="test_secret")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "auth_token_invalid"


def test_project_api_create_seeds_local_upload_repository() -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    project_repository = InMemoryProjectRepository()
    upload_repository = InMemoryUploadRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(upload_repository=upload_repository),
        ),
    )

    project = asyncio.run(
        create_project(
            payload=ProjectCreateRequest(title=" Launch Reel "),
            request=request,
            actor=SimpleNamespace(user_id=user_id, workspace_id=workspace_id),
            repository=project_repository,
        )
    )
    projects = asyncio.run(
        list_projects(
            actor=SimpleNamespace(user_id=user_id, workspace_id=workspace_id),
            repository=project_repository,
        )
    )

    assert project.title == "Launch Reel"
    assert projects.projects[0].id == project.id
    assert upload_repository.get_project_for_workspace(
        project_id=project.id,
        workspace_id=workspace_id,
    )


def test_project_api_prefers_async_repository_methods() -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    upload_repository = InMemoryUploadRepository()

    class AsyncProjectRepository(InMemoryProjectRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def create_project_async(self, **kwargs):
            self.async_calls.append("create")
            return self.create_project(**kwargs)

        async def list_projects_async(self, **kwargs):
            self.async_calls.append("list")
            return self.list_projects(**kwargs)

        async def get_project_async(self, **kwargs):
            self.async_calls.append("get")
            return self.get_project(**kwargs)

        async def update_project_async(self, **kwargs):
            self.async_calls.append("update")
            return self.update_project(**kwargs)

        async def delete_project_async(self, **kwargs):
            self.async_calls.append("delete")
            return self.delete_project(**kwargs)

    project_repository = AsyncProjectRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(upload_repository=upload_repository),
        ),
    )
    actor = SimpleNamespace(user_id=user_id, workspace_id=workspace_id)

    project = asyncio.run(
        create_project(
            payload=ProjectCreateRequest(title="Async Reel"),
            request=request,
            actor=actor,
            repository=project_repository,
        )
    )
    listed = asyncio.run(
        list_projects(
            actor=actor,
            repository=project_repository,
        )
    )
    fetched = asyncio.run(
        get_project(
            project_id=project.id,
            actor=actor,
            repository=project_repository,
        )
    )
    asyncio.run(
        update_project(
            project_id=project.id,
            payload=ProjectUpdateRequest(title="Async Reel 2", status=ProjectStatus.READY),
            actor=actor,
            repository=project_repository,
        )
    )
    asyncio.run(delete_project(project_id=project.id, actor=actor, repository=project_repository))

    assert listed.projects[0].id == project.id
    assert fetched.id == project.id
    assert project_repository.async_calls == ["create", "list", "get", "update", "delete"]


def test_project_api_update_and_delete_project() -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    project_repository = InMemoryProjectRepository()
    upload_repository = InMemoryUploadRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(upload_repository=upload_repository),
        ),
    )
    actor = SimpleNamespace(user_id=user_id, workspace_id=workspace_id)

    project = asyncio.run(
        create_project(
            payload=ProjectCreateRequest(title="Draft Reel"),
            request=request,
            actor=actor,
            repository=project_repository,
        )
    )
    updated = asyncio.run(
        update_project(
            project_id=project.id,
            payload=ProjectUpdateRequest(title="Final Reel", status=ProjectStatus.READY),
            actor=actor,
            repository=project_repository,
        )
    )
    deleted = asyncio.run(
        delete_project(
            project_id=project.id,
            actor=actor,
            repository=project_repository,
        )
    )

    assert updated.title == "Final Reel"
    assert updated.status == ProjectStatus.READY
    assert deleted.id == project.id
    assert deleted.deleted is True
    with pytest.raises(ApiError):
        project_repository.get_project(project_id=project.id, workspace_id=workspace_id)


def _request(
    *,
    app_env: str,
    supabase_jwt_secret: str | None,
    supabase_url: str | None = None,
    supabase_service_role_key: str | None = None,
):
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env=app_env,
                    supabase_jwt_secret=supabase_jwt_secret,
                    supabase_url=supabase_url,
                    supabase_service_role_key=supabase_service_role_key,
                ),
            ),
        ),
    )


def _jwt(claims: dict[str, object], secret: str = "secret", algorithm: str = "HS256") -> str:
    header = {"alg": algorithm, "typ": "JWT"}
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{encoded_header}.{encoded_claims}".encode(),
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")
