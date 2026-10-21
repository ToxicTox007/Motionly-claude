"""Management routes for workspace API keys (Task 24).

Owners and admins of an Agency-plan workspace can mint, list and revoke
``mtly_`` API keys here. The raw secret is returned exactly once, at creation.
These routes are called by the browser (a signed-in owner/admin), not by the
API keys themselves — they are *not* on the API-key allow-list.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import Actor, get_actor, get_billing_repository, require_role
from app.repositories.api_keys import (
    InMemoryApiKeyRepository,
    generate_api_key,
)
from app.repositories.billing import InMemoryBillingRepository
from app.schemas.api_keys import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
)
from app.services.api_key_access import require_api_access
from app.services.offload import run_blocking

router = APIRouter(
    prefix="/api-keys",
    tags=["api-keys"],
    # Managing keys is an owner/admin action (Task 19 require_role).
    dependencies=[Depends(require_role("owner", "admin"))],
)

ActorDep = Annotated[Actor, Depends(get_actor)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]


def _get_api_key_repository(request: Request) -> InMemoryApiKeyRepository:
    return request.app.state.api_key_repository


ApiKeyRepositoryDep = Annotated[InMemoryApiKeyRepository, Depends(_get_api_key_repository)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


async def _effective_subscription(billing_repository, workspace_id: UUID):
    return await _repository_call(
        billing_repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=workspace_id,
    )


@router.post("", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    actor: ActorDep,
    repository: ApiKeyRepositoryDep,
    billing_repository: BillingRepositoryDep,
) -> ApiKeyCreateResponse:
    subscription = await _effective_subscription(billing_repository, actor.workspace_id)
    # Only plans with the api_access entitlement (Agency) may mint keys.
    require_api_access(subscription)

    raw_key, fingerprint, prefix, last4 = generate_api_key()
    record = await _repository_call(
        repository,
        "create_key_async",
        "create_key",
        workspace_id=actor.workspace_id,
        name=payload.name.strip(),
        fingerprint=fingerprint,
        prefix=prefix,
        last4=last4,
        created_by=actor.user_id,
    )
    # The raw secret is returned here once and never persisted or logged.
    return ApiKeyCreateResponse(key=record.to_response(), secret=raw_key)


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    actor: ActorDep,
    repository: ApiKeyRepositoryDep,
) -> ApiKeyListResponse:
    records = await _repository_call(
        repository,
        "list_keys_async",
        "list_keys",
        workspace_id=actor.workspace_id,
    )
    return ApiKeyListResponse(keys=[record.to_response() for record in records])


@router.post("/{key_id}/revoke", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: UUID,
    actor: ActorDep,
    repository: ApiKeyRepositoryDep,
) -> ApiKeyResponse:
    record = await _repository_call(
        repository,
        "revoke_key_async",
        "revoke_key",
        workspace_id=actor.workspace_id,
        key_id=key_id,
    )
    return record.to_response()
