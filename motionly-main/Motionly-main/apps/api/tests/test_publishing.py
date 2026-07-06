import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.publishing import create_publish_job, list_connections, list_publish_jobs
from app.repositories.publishing import (
    PUBLISH_CONNECTION_COLUMNS,
    PUBLISH_JOB_COLUMNS,
    InMemoryPublishingRepository,
    SupabasePublishingRepository,
)
from app.schemas.publishing import (
    PublishConnectionStatus,
    PublishCreateRequest,
    PublishPlatform,
    PublishStatus,
)
from app.services.publishing import (
    ProviderPublishError,
    PublishedPost,
    SocialPublishingClient,
    _provider_error_to_api_error,
    create_oauth_state,
    execute_publish_job,
    read_oauth_state,
)
from app.services.queue import PublishQueue, QueueRegistry, RenderQueue


def test_gpu_render_and_publish_jobs_route_to_dedicated_queues() -> None:
    registry = QueueRegistry()
    render_job = RenderQueue(registry).enqueue_render(
        render_job_id=uuid4(),
        project_id=uuid4(),
        workspace_id=uuid4(),
        compute_tier="gpu",
    )
    publish_job = PublishQueue(registry).enqueue_publish(
        publish_job_id=uuid4(),
        project_id=uuid4(),
        workspace_id=uuid4(),
    )

    assert render_job.queue_name == "render_gpu"
    assert publish_job.queue_name == "publish"


def test_publish_job_executes_with_signed_export_and_redacts_connection_token() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    export_id = uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="top-secret-access-token",
        external_account_id="channel-1",
        account_name="Motionly Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Video title",
        caption="Caption",
        privacy="private",
    )

    class RenderRepository:
        def get_export(self, *, export_id, workspace_id):
            return SimpleNamespace(storage_bucket="exports", storage_path="project/video.mp4")

    class StorageSigner:
        def create_signed_download(self, **_kwargs):
            return SimpleNamespace(url="https://cdn.example.com/signed.mp4")

    class Client:
        def publish(self, *, connection, job, video_url):
            assert connection.access_token == "top-secret-access-token"
            assert video_url == "https://cdn.example.com/signed.mp4"
            return PublishedPost(
                external_post_id="youtube-123", external_url="https://youtu.be/youtube-123"
            )

    completed = execute_publish_job(
        publishing_repository=repository,
        render_repository=RenderRepository(),
        storage_signer=StorageSigner(),
        client=Client(),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert completed.status == PublishStatus.PUBLISHED
    assert completed.external_post_id == "youtube-123"
    assert "access_token" not in connection.to_response().model_dump()


def test_publish_api_wires_export_connection_and_worker_queue() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    export_id = uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.TIKTOK,
        access_token="tiktok-access-token",
        external_account_id="creator-1",
        account_name="Creator",
    )
    registry = QueueRegistry()

    class ProjectRepository:
        def get_project(self, **_kwargs):
            return SimpleNamespace(id=project_id)

    class RenderRepository:
        def get_export(self, **_kwargs):
            return SimpleNamespace(id=export_id, project_id=project_id)

    state = SimpleNamespace(
        project_repository=ProjectRepository(),
        render_repository=RenderRepository(),
        publishing_repository=repository,
        publish_queue=PublishQueue(registry),
        settings=SimpleNamespace(publish_sync_completion=False),
    )
    response = asyncio.run(
        create_publish_job(
            project_id=project_id,
            payload=PublishCreateRequest(
                export_id=export_id,
                connection_id=connection.id,
                title="Launch clip",
                caption="#motionly",
                privacy="private",
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            request=SimpleNamespace(app=SimpleNamespace(state=state)),
        )
    )

    assert response.platform == PublishPlatform.TIKTOK
    assert response.status == PublishStatus.QUEUED
    queued = next(iter(registry.jobs.values()))
    assert queued.kind == "publish"
    assert queued.resource_id == response.id


def test_publish_connection_and_job_lists_apply_limit() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    export_id = uuid4()
    repository = InMemoryPublishingRepository()
    first_connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="youtube-token",
        external_account_id="channel-1",
        account_name="YouTube",
    )
    second_connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.TIKTOK,
        access_token="tiktok-token",
        external_account_id="creator-1",
        account_name="TikTok",
    )
    first_job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=first_connection.id,
        platform=first_connection.platform,
        status=PublishStatus.QUEUED,
        title="First",
        caption="",
        privacy="private",
    )
    second_job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=second_connection.id,
        platform=second_connection.platform,
        status=PublishStatus.PUBLISHING,
        title="Second",
        caption="",
        privacy="private",
    )

    class ProjectRepository:
        def get_project(self, **_kwargs):
            return SimpleNamespace(id=project_id)

    state = SimpleNamespace(
        project_repository=ProjectRepository(),
        publishing_repository=repository,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    connections = asyncio.run(list_connections(actor=actor, request=request, limit=1))
    jobs = asyncio.run(
        list_publish_jobs(project_id=project_id, actor=actor, request=request, limit=1)
    )

    assert [connection.id for connection in connections.connections] == [second_connection.id]
    assert [job.id for job in jobs.jobs] == [second_job.id]
    assert first_job.id != second_job.id


@pytest.mark.anyio
async def test_supabase_publishing_async_lists_use_bounded_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    export_id = uuid4()
    connection_id = uuid4()
    job_id = uuid4()
    calls = []

    class PlainCipher:
        def encrypt(self, value):
            return value

        def decrypt(self, value):
            return value

        def is_encrypted(self, _value):
            return True

    repository = SupabasePublishingRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        token_cipher=PlainCipher(),
    )

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/publish_connections":
            return _supabase_response([_connection_row(connection_id, workspace_id)])
        if path == "/publish_jobs":
            return _supabase_response(
                [_publish_job_row(job_id, workspace_id, project_id, export_id, connection_id)]
            )
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr(
        "app.repositories.publishing.SupabaseRestClient.async_request",
        fake_async_request,
    )

    connections = await repository.list_connections_async(workspace_id=workspace_id, limit=25)
    jobs = await repository.list_jobs_async(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=10,
    )
    active = await repository.find_active_job_async(
        workspace_id=workspace_id,
        export_id=export_id,
        connection_id=connection_id,
    )

    assert connections[0].id == connection_id
    assert jobs[0].id == job_id
    assert active is not None
    assert calls[0][2]["params"] == {
        "workspace_id": f"eq.{workspace_id}",
        "select": PUBLISH_CONNECTION_COLUMNS,
        "order": "created_at.desc",
        "limit": "25",
    }
    assert calls[1][2]["params"] == {
        "project_id": f"eq.{project_id}",
        "workspace_id": f"eq.{workspace_id}",
        "select": PUBLISH_JOB_COLUMNS,
        "order": "created_at.desc",
        "limit": "10",
    }
    assert calls[2][2]["params"]["select"] == PUBLISH_JOB_COLUMNS
    assert calls[2][2]["params"]["limit"] == "1"


def test_publishing_api_prefers_async_repository_methods() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    export_id = uuid4()

    class AsyncPublishingRepository(InMemoryPublishingRepository):
        def __init__(self):
            super().__init__()
            self.async_calls: list[str] = []

        async def list_connections_async(self, **kwargs):
            self.async_calls.append("list-connections")
            return self.list_connections(**kwargs)

        async def list_jobs_async(self, **kwargs):
            self.async_calls.append("list-jobs")
            return self.list_jobs(**kwargs)

        async def get_connection_async(self, **kwargs):
            self.async_calls.append("get-connection")
            return self.get_connection(**kwargs)

        async def find_active_job_async(self, **kwargs):
            self.async_calls.append("find-active")
            return self.find_active_job(**kwargs)

        async def create_job_async(self, **kwargs):
            self.async_calls.append("create-job")
            return self.create_job(**kwargs)

    repository = AsyncPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="token",
        external_account_id="channel",
        account_name="Channel",
    )

    class ProjectRepository:
        def get_project(self, **_kwargs):
            return SimpleNamespace(id=project_id)

    class RenderRepository:
        def get_export(self, **_kwargs):
            return SimpleNamespace(id=export_id, project_id=project_id)

    state = SimpleNamespace(
        project_repository=ProjectRepository(),
        render_repository=RenderRepository(),
        publishing_repository=repository,
        publish_queue=SimpleNamespace(enqueue_publish=lambda **_kwargs: None),
        settings=SimpleNamespace(publish_sync_completion=False, app_env="test"),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    asyncio.run(list_connections(actor=actor, request=request, limit=5))
    asyncio.run(list_publish_jobs(project_id=project_id, actor=actor, request=request, limit=5))
    created = asyncio.run(
        create_publish_job(
            project_id=project_id,
            payload=PublishCreateRequest(
                export_id=export_id,
                connection_id=connection.id,
                title="Title",
                caption="Caption",
                privacy="private",
            ),
            actor=actor,
            request=request,
        )
    )

    assert created.status == PublishStatus.QUEUED
    assert repository.async_calls == [
        "list-connections",
        "list-jobs",
        "get-connection",
        "find-active",
        "create-job",
    ]


def test_publish_oauth_state_is_signed_and_scoped_to_workspace() -> None:
    workspace_id = uuid4()
    state = create_oauth_state(
        secret="oauth-state-secret",
        workspace_id=workspace_id,
        platform=PublishPlatform.INSTAGRAM,
        return_url="https://app.motionly.example/editor/project-1",
    )

    values = read_oauth_state(secret="oauth-state-secret", state=state)

    assert values["workspace_id"] == str(workspace_id)
    assert values["platform"] == "instagram"
    assert values["return_url"].startswith("https://app.motionly.example/")


def test_execute_publish_job_is_idempotent_when_job_already_published() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="token",
        external_account_id="channel-1",
        account_name="Channel",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.PUBLISHED,
        title="Video",
        caption="",
        privacy="private",
    )

    class Client:
        def publish(self, **_kwargs):
            raise AssertionError("published jobs must not republish")

    completed = execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(get_export=lambda **_kwargs: None),
        storage_signer=SimpleNamespace(create_signed_download=lambda **_kwargs: None),
        client=Client(),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert completed.status == PublishStatus.PUBLISHED


def test_create_publish_job_rejects_duplicate_queued_publish_for_same_export_and_connection(
) -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    export_id = uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.TIKTOK,
        access_token="tiktok-access-token",
        external_account_id="creator-1",
        account_name="Creator",
    )
    repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Existing",
        caption="",
        privacy="private",
    )
    state = SimpleNamespace(
        project_repository=SimpleNamespace(
            get_project=lambda **_kwargs: SimpleNamespace(id=project_id)
        ),
        render_repository=SimpleNamespace(
            get_export=lambda **_kwargs: SimpleNamespace(id=export_id, project_id=project_id)
        ),
        publishing_repository=repository,
        publish_queue=SimpleNamespace(enqueue_publish=lambda **_kwargs: None),
        settings=SimpleNamespace(publish_sync_completion=False),
    )

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            create_publish_job(
                project_id=project_id,
                payload=PublishCreateRequest(
                    export_id=export_id,
                    connection_id=connection.id,
                    title="Launch clip",
                    caption="#motionly",
                    privacy="private",
                ),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                request=SimpleNamespace(app=SimpleNamespace(state=state)),
            )
        )

    assert exc.value.code == "publish_job_already_in_progress"


def test_publish_job_marks_reconnect_required_when_connection_already_revoked() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.YOUTUBE,
        access_token="expired-token",
        external_account_id="channel",
        account_name="Channel",
        connection_status=PublishConnectionStatus.RECONNECT_REQUIRED,
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

    completed = execute_publish_job(
        publishing_repository=repository,
        render_repository=SimpleNamespace(get_export=lambda **_kwargs: None),
        storage_signer=SimpleNamespace(create_signed_download=lambda **_kwargs: None),
        client=SimpleNamespace(publish=lambda **_kwargs: None),
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert completed.status == PublishStatus.RECONNECT_REQUIRED
    assert completed.reconnect_required is True


def test_publish_job_schedules_bounded_retry_for_provider_rate_limit() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.TIKTOK,
        access_token="token",
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
        title="Video",
        caption="",
        privacy="private",
    )

    class Client:
        def publish(self, **_kwargs):
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=PublishPlatform.TIKTOK,
                    error_code="provider_rate_limited",
                    error_category="provider_rate_limited",
                    retryable=True,
                    reconnect_required=False,
                    user_message="TikTok rate limited this publish. We'll retry automatically.",
                    safe_internal_message="rate limited",
                    retry_after_seconds=120,
                )
            )

    with pytest.raises(ApiError) as exc:
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
            settings=SimpleNamespace(
                publish_retry_backoff_seconds=60,
                publish_retry_backoff_cap_seconds=300,
                publish_retry_max_attempts=3,
            ),
            job_id=job.id,
            workspace_id=workspace_id,
        )

    assert exc.value.code == "provider_rate_limited"
    updated = repository.get_job(job_id=job.id, workspace_id=workspace_id)
    assert updated.status == PublishStatus.RETRY_SCHEDULED
    assert updated.retry_count == 1
    assert updated.retry_after_seconds == 120


def test_publish_job_marks_duplicate_risk_for_uncertain_provider_progress() -> None:
    workspace_id, project_id, export_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPublishingRepository()
    connection = repository.create_connection(
        workspace_id=workspace_id,
        platform=PublishPlatform.INSTAGRAM,
        access_token="token",
        external_account_id="account",
        account_name="Account",
    )
    job = repository.create_job(
        workspace_id=workspace_id,
        project_id=project_id,
        export_id=export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title="Video",
        caption="",
        privacy="private",
    )

    class Client:
        def publish(self, **_kwargs):
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=PublishPlatform.INSTAGRAM,
                    error_code="duplicate_publish_risk",
                    error_category="processing_failed",
                    retryable=False,
                    reconnect_required=False,
                    user_message="Instagram processing state is uncertain.",
                    safe_internal_message="processing uncertain",
                    duplicate_publish_risk=True,
                    provider_operation_id="creation-123",
                )
            )

    completed = execute_publish_job(
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
        job_id=job.id,
        workspace_id=workspace_id,
    )

    assert completed.status == PublishStatus.DUPLICATE_RISK_NEEDS_REVIEW
    assert completed.duplicate_publish_risk is True
    assert completed.provider_operation_id == "creation-123"


def test_social_publishing_client_maps_rate_limit_with_retry_after() -> None:
    client = SocialPublishingClient()
    response = SimpleNamespace(
        status_code=429,
        headers={"retry-after": "90"},
        json=lambda: {"message": "Too many requests"},
    )

    with pytest.raises(ApiError) as exc:
        client._json_response(response, platform=PublishPlatform.YOUTUBE, phase="upload")

    assert exc.value.code == "provider_rate_limited"
    assert exc.value.details["retry_after_seconds"] == 90


def test_social_publishing_client_maps_insufficient_scope_to_reconnect() -> None:
    client = SocialPublishingClient()
    response = SimpleNamespace(
        status_code=403,
        headers={},
        json=lambda: {"error": {"message": "Insufficient scope to publish"}},
    )

    with pytest.raises(ApiError) as exc:
        client._json_response(response, platform=PublishPlatform.YOUTUBE, phase="upload")

    assert exc.value.code == "insufficient_scope"
    assert exc.value.details["reconnect_required"] is True


def _supabase_response(rows):
    return SimpleNamespace(status_code=200, content=b"[]", json=lambda: rows)


def _connection_row(connection_id, workspace_id):
    return {
        "id": str(connection_id),
        "workspace_id": str(workspace_id),
        "platform": "youtube",
        "access_token": "token",
        "refresh_token": None,
        "token_expires_at": None,
        "refresh_token_expires_at": None,
        "scopes": [],
        "connection_status": "active",
        "last_refresh_at": None,
        "last_refresh_failure_reason": None,
        "external_account_id": "channel",
        "account_name": "Channel",
        "created_at": "2026-07-06T00:00:00+00:00",
    }


def _publish_job_row(job_id, workspace_id, project_id, export_id, connection_id):
    now = "2026-07-06T00:00:00+00:00"
    return {
        "id": str(job_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "export_id": str(export_id),
        "connection_id": str(connection_id),
        "platform": "youtube",
        "status": "queued",
        "title": "Title",
        "caption": "Caption",
        "privacy": "private",
        "external_post_id": None,
        "external_url": None,
        "provider_operation_id": None,
        "error_message": None,
        "error_code": None,
        "error_category": None,
        "user_message": None,
        "retryable": None,
        "reconnect_required": False,
        "retry_after_seconds": None,
        "retry_count": 0,
        "duplicate_publish_risk": False,
        "created_at": now,
        "updated_at": now,
    }
