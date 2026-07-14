from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.repositories.publishing import InMemoryPublishingRepository
from app.schemas.publishing import (
    PublishConnectionCreateRequest,
    PublishConnectionListResponse,
    PublishConnectionResponse,
    PublishConnectionStatus,
    PublishCreateRequest,
    PublishJobListResponse,
    PublishJobResponse,
    PublishOAuthStartRequest,
    PublishOAuthStartResponse,
    PublishPlatform,
    PublishStatus,
)
from app.services.layer_job_enqueue import prepare_heavy_job_enqueue
from app.services.publishing import (
    PublishOAuthClient,
    create_oauth_state,
    execute_publish_job,
    read_oauth_state,
)

router = APIRouter(tags=["publishing"])
ActorDep = Annotated[Actor, Depends(get_actor)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.post(
    "/publish/oauth/{platform}/start",
    response_model=PublishOAuthStartResponse,
)
async def start_publish_oauth(
    platform: PublishPlatform,
    payload: PublishOAuthStartRequest,
    actor: ActorDep,
    request: Request,
) -> PublishOAuthStartResponse:
    settings = request.app.state.settings
    return_url = str(payload.return_url)
    _validate_return_url(return_url=return_url, settings=settings)
    secret = _oauth_state_secret(settings)
    redirect_uri = _oauth_redirect_uri(request=request, platform=platform)
    state = create_oauth_state(
        secret=secret,
        workspace_id=actor.workspace_id,
        platform=platform,
        return_url=return_url,
    )
    authorization_url = PublishOAuthClient(settings).authorization_url(
        platform=platform,
        redirect_uri=redirect_uri,
        state=state,
    )
    return PublishOAuthStartResponse(authorization_url=authorization_url)


@router.get("/publish/oauth/{platform}/callback", response_class=RedirectResponse)
async def complete_publish_oauth(
    platform: PublishPlatform,
    code: str,
    state: str,
    request: Request,
) -> RedirectResponse:
    settings = request.app.state.settings
    values = read_oauth_state(secret=_oauth_state_secret(settings), state=state)
    if values["platform"] != platform.value:
        raise ApiError(400, "publish_oauth_state_invalid", "Publishing platform does not match.")
    return_url = values["return_url"]
    _validate_return_url(return_url=return_url, settings=settings)
    account = PublishOAuthClient(settings).exchange(
        platform=platform,
        code=code,
        redirect_uri=_oauth_redirect_uri(request=request, platform=platform),
    )
    await _repository_call(
        request.app.state.publishing_repository,
        "upsert_connection_async",
        "upsert_connection",
        workspace_id=UUID(values["workspace_id"]),
        platform=platform,
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        token_expires_at=account.token_expires_at,
        refresh_token_expires_at=account.refresh_token_expires_at,
        scopes=account.scopes,
        external_account_id=account.external_account_id,
        account_name=account.account_name,
        connection_status=PublishConnectionStatus.ACTIVE,
        last_refresh_at=None,
        last_refresh_failure_reason=None,
    )
    return RedirectResponse(
        _append_query(return_url, publish_connected=platform.value), status_code=303
    )


@router.get("/publish/connections", response_model=PublishConnectionListResponse)
async def list_connections(
    actor: ActorDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> PublishConnectionListResponse:
    repository: InMemoryPublishingRepository = request.app.state.publishing_repository
    connections = await _repository_call(
        repository,
        "list_connections_async",
        "list_connections",
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return PublishConnectionListResponse(
        connections=[item.to_response() for item in connections]
    )


@router.post("/publish/connections", response_model=PublishConnectionResponse, status_code=201)
async def create_connection(
    payload: PublishConnectionCreateRequest, actor: ActorDep, request: Request
) -> PublishConnectionResponse:
    repository: InMemoryPublishingRepository = request.app.state.publishing_repository
    record = await _repository_call(
        repository,
        "create_connection_async",
        "create_connection",
        workspace_id=actor.workspace_id,
        **payload.model_dump(),
    )
    return record.to_response()


@router.delete("/publish/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: UUID, actor: ActorDep, request: Request) -> Response:
    await _repository_call(
        request.app.state.publishing_repository,
        "delete_connection_async",
        "delete_connection",
        connection_id=connection_id,
        workspace_id=actor.workspace_id,
    )
    return Response(status_code=204)


@router.get("/projects/{project_id}/publish", response_model=PublishJobListResponse)
async def list_publish_jobs(
    project_id: UUID,
    actor: ActorDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> PublishJobListResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    jobs = await _repository_call(
        request.app.state.publishing_repository,
        "list_jobs_async",
        "list_jobs",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return PublishJobListResponse(jobs=[item.to_response() for item in jobs])


@router.post("/projects/{project_id}/publish", response_model=PublishJobResponse, status_code=202)
async def create_publish_job(
    project_id: UUID, payload: PublishCreateRequest, actor: ActorDep, request: Request
) -> PublishJobResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    export = request.app.state.render_repository.get_export(
        export_id=payload.export_id, workspace_id=actor.workspace_id
    )
    if export.project_id != project_id:
        raise ApiError(
            422, "publish_export_project_mismatch", "Export does not belong to this project."
        )
    connection = await _repository_call(
        request.app.state.publishing_repository,
        "get_connection_async",
        "get_connection",
        connection_id=payload.connection_id,
        workspace_id=actor.workspace_id,
    )
    active_job = await _repository_call(
        request.app.state.publishing_repository,
        "find_active_job_async",
        "find_active_job",
        workspace_id=actor.workspace_id,
        export_id=payload.export_id,
        connection_id=connection.id,
    )
    if active_job is not None:
        raise ApiError(
            409,
            "publish_job_already_in_progress",
            "This export is already being published to that account.",
        )
    record = await _repository_call(
        request.app.state.publishing_repository,
        "create_job_async",
        "create_job",
        workspace_id=actor.workspace_id,
        project_id=project_id,
        export_id=payload.export_id,
        connection_id=connection.id,
        platform=connection.platform,
        status=PublishStatus.QUEUED,
        title=payload.title,
        caption=payload.caption,
        privacy=payload.privacy,
    )
    prepare_heavy_job_enqueue(request=request)
    request.app.state.publish_queue.enqueue_publish(
        publish_job_id=record.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if (
        request.app.state.settings.publish_sync_completion
        and request.app.state.settings.app_env != "production"
    ):
        record = execute_publish_job(
            publishing_repository=request.app.state.publishing_repository,
            render_repository=request.app.state.render_repository,
            storage_signer=request.app.state.storage_signer,
            client=request.app.state.social_publishing_client,
            oauth_client=request.app.state.publish_oauth_client,
            settings=request.app.state.settings,
            job_id=record.id,
            workspace_id=actor.workspace_id,
        )
    return record.to_response()


def _oauth_state_secret(settings) -> str:
    secret = settings.publish_oauth_state_secret
    if not secret:
        raise ApiError(503, "publish_oauth_not_configured", "Publishing OAuth is not configured.")
    return secret


def _oauth_redirect_uri(*, request: Request, platform: PublishPlatform) -> str:
    base_url = str(request.app.state.settings.api_public_base_url).rstrip("/")
    return f"{base_url}/publish/oauth/{platform.value}/callback"


def _validate_return_url(*, return_url: str, settings) -> None:
    parsed = urlparse(return_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError(422, "publish_return_url_invalid", "Return URL is invalid.")
    if settings.app_env == "production" and origin.rstrip("/") not in settings.cors_origin_list:
        raise ApiError(422, "publish_return_url_invalid", "Return URL origin is not allowed.")


def _append_query(url: str, **values: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(values)
    return urlunparse(parsed._replace(query=urlencode(query)))
