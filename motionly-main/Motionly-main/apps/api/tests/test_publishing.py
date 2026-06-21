import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.dependencies import Actor
from app.api.publishing import create_publish_job
from app.repositories.publishing import InMemoryPublishingRepository
from app.schemas.publishing import PublishCreateRequest, PublishPlatform, PublishStatus
from app.services.publishing import (
    PublishedPost,
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
