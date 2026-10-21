from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from app.api.api_key_routes import route_is_api_key_allowed
from app.api.errors import ApiError
from app.core.config import LOCAL_AUTH_BYPASS_ENVS
from app.repositories.ai_proposals import InMemoryAIProposalRepository
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.api_keys import KEY_SCHEME_PREFIX, key_fingerprint
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.effects import InMemoryEffectsRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.projects import InMemoryProjectRepository
from app.repositories.regeneration import InMemoryRegenerationRepository
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.reviews import InMemoryReviewRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    SupabaseWorkflowRepository,
)
from app.services.auth import (
    WORKSPACE_ROLES,
    require_workspace_membership,
    verify_supabase_jwt,
    verify_supabase_token_with_auth_api,
)
from app.services.edit_planner import EditPlannerProvider
from app.services.music import MusicProvider
from app.services.offload import run_blocking
from app.services.payment_gateway import PaymentGateway
from app.services.queue import (
    BrollQueue,
    EffectsQueue,
    MatteQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    RegenerationQueue,
    RenderQueue,
    StockAssetEnrichmentQueue,
    TranscriptionQueue,
    VisualQueue,
)
from app.services.stock_video import StockVideoProvider
from app.services.storage import StorageSigner
from app.services.transcription_provider import TranscriptionProvider
from app.services.visual_intelligence import VisualIntelligenceProvider


@dataclass(frozen=True)
class Actor:
    user_id: UUID
    workspace_id: UUID
    # The caller's workspace_role: "owner", "admin" or "member". Resolved from
    # workspace_members by require_workspace_membership (Task 19).
    role: str = "owner"
    # How the caller authenticated: "user" (a signed-in Supabase session) or
    # "api_key" (an ``mtly_`` Agency API key). API-key callers are restricted
    # to the documented allow-list (app.api.api_key_routes). Task 24.
    kind: str = "user"
    # When ``kind == "api_key"``, the id of the key row (for rate limiting and
    # audit); otherwise None.
    api_key_id: UUID | None = None


async def get_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_motionly_workspace_id: str | None = Header(default=None),
) -> Actor:
    settings = request.app.state.settings
    if getattr(settings, "api_auth_bypass_enabled", False):
        return Actor(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
            role="owner",
        )

    if not settings.supabase_jwt_secret:
        raise ApiError(500, "auth_not_configured", "API authentication is not configured.")

    if settings.supabase_jwt_secret:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "auth_required", "Sign in before using Motionly.")
        token = authorization.split(" ", 1)[1].strip()
        # Agency API keys (``mtly_...``) authenticate automation. They resolve to
        # a distinct actor kind and are restricted to the documented allow-list.
        if token.startswith(KEY_SCHEME_PREFIX):
            return await _resolve_api_key_actor(request, token)
        request_state = getattr(request, "state", None)
        verified = getattr(request_state, "verified_supabase_token", None)
        if verified is None:
            try:
                verified = verify_supabase_jwt(token=token, jwt_secret=settings.supabase_jwt_secret)
            except ApiError as exc:
                if (
                    exc.code != "auth_token_invalid"
                    or "algorithm" not in exc.message.lower()
                    or not settings.supabase_url
                    or not settings.supabase_service_role_key
                ):
                    raise
                verified = verify_supabase_token_with_auth_api(
                    token=token,
                    supabase_url=str(settings.supabase_url),
                    service_role_key=settings.supabase_service_role_key,
                )
        if not x_motionly_workspace_id:
            raise ApiError(401, "auth_context_missing", "Workspace context is required.")
        try:
            workspace_id = UUID(x_motionly_workspace_id)
        except ValueError as exc:
            raise ApiError(401, "auth_context_invalid", "Workspace context is invalid.") from exc
        role = "owner"
        if settings.supabase_url and settings.supabase_service_role_key:
            role = (
                await run_blocking(
                    require_workspace_membership,
                    supabase_url=str(settings.supabase_url),
                    service_role_key=settings.supabase_service_role_key,
                    user_id=verified.user_id,
                    workspace_id=workspace_id,
                    metrics_registry=getattr(request.app.state, "metrics_registry", None),
                )
                or "owner"
            )
        elif settings.app_env not in LOCAL_AUTH_BYPASS_ENVS:
            raise ApiError(500, "auth_not_configured", "Workspace auth is not configured.")
        return Actor(user_id=verified.user_id, workspace_id=workspace_id, role=role)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return request.url.path


async def _resolve_api_key_actor(request: Request, token: str) -> Actor:
    """Resolve a ``mtly_`` bearer token to an API-key Actor.

    Looks the key up by SHA-256 fingerprint (constant-time compare in the
    repository), refuses revoked/unknown keys with 401, and refuses routes that
    are not on the documented allow-list with 403. The key's last-used stamp is
    best-effort updated.
    """
    repository = getattr(request.app.state, "api_key_repository", None)
    if repository is None:
        raise ApiError(401, "auth_token_invalid", "API key authentication is unavailable.")

    fingerprint = key_fingerprint(token)
    record = await run_blocking(repository.resolve_live_key, fingerprint=fingerprint)
    # Unknown and revoked keys are indistinguishable to the caller (both 401).
    if record is None or not record.is_live:
        raise ApiError(401, "api_key_invalid", "API key is invalid or has been revoked.")

    if not route_is_api_key_allowed(request.method, _route_template(request)):
        raise ApiError(
            403,
            "api_key_route_forbidden",
            "This endpoint is not available to API keys.",
        )

    # Best-effort last-used stamp; never block the request on it.
    try:
        await run_blocking(repository.touch_last_used, key_id=record.id)
    except Exception:  # noqa: BLE001 - last_used is advisory, not load-bearing
        pass

    return Actor(
        user_id=record.created_by,
        workspace_id=record.workspace_id,
        # API keys act with owner-equivalent authority within their allowed
        # routes; the allow-list is the real boundary, not the workspace role.
        role="owner",
        kind="api_key",
        api_key_id=record.id,
    )


ActorDep = Annotated[Actor, Depends(get_actor)]


def require_role(*allowed_roles: str) -> Callable[[Actor], Actor]:
    """Build a dependency that admits only the given workspace roles.

    Usage (route- or router-level)::

        @router.post("/billing/checkout", dependencies=[Depends(require_role("owner"))])
        ...
        router = APIRouter(dependencies=[Depends(require_role("owner", "admin"))])

    Raises 403 ``workspace_role_forbidden`` when the caller's role is not in
    ``allowed_roles``. Task 24 reuses this for API-key routes once they exist.
    """
    if not allowed_roles:
        raise ValueError("require_role needs at least one role")
    for role in allowed_roles:
        if role not in WORKSPACE_ROLES:
            raise ValueError(f"unknown workspace role: {role!r}")
    allowed = frozenset(allowed_roles)

    async def _dependency(actor: ActorDep) -> Actor:
        if actor.role not in allowed:
            raise ApiError(
                403,
                "workspace_role_forbidden",
                "Your workspace role is not allowed to perform this action.",
            )
        return actor

    # Tag so route-inventory tests can read the enforced roles off a route.
    _dependency.__motionly_required_roles__ = allowed
    return _dependency


def get_upload_repository(request: Request) -> InMemoryUploadRepository:
    return request.app.state.upload_repository


def get_project_repository(request: Request) -> InMemoryProjectRepository:
    return request.app.state.project_repository


def get_storage_signer(request: Request) -> StorageSigner:
    return request.app.state.storage_signer


def get_preprocess_queue(request: Request) -> PreprocessQueue:
    return request.app.state.preprocess_queue


def get_upload_session_service(request: Request):
    return request.app.state.upload_session_service


def get_transcription_queue(request: Request) -> TranscriptionQueue:
    return request.app.state.transcription_queue


def get_visual_queue(request: Request) -> VisualQueue:
    return request.app.state.visual_queue


def get_matte_queue(request: Request) -> MatteQueue:
    return request.app.state.matte_queue


def get_plan_queue(request: Request) -> PlanQueue:
    return request.app.state.plan_queue


def get_broll_queue(request: Request) -> BrollQueue:
    return request.app.state.broll_queue


def get_stock_asset_enrichment_queue(request: Request) -> StockAssetEnrichmentQueue:
    return request.app.state.stock_asset_enrichment_queue


def get_render_queue(request: Request) -> RenderQueue:
    return request.app.state.render_queue


def get_caption_repository(request: Request) -> InMemoryCaptionRepository:
    return request.app.state.caption_repository


def get_render_repository(request: Request) -> InMemoryRenderRepository:
    return request.app.state.render_repository


def get_export_share_repository(request: Request):
    return request.app.state.export_share_repository


def get_regeneration_repository(request: Request) -> InMemoryRegenerationRepository:
    return request.app.state.regeneration_repository


def get_review_repository(request: Request) -> InMemoryReviewRepository:
    return request.app.state.review_repository


def get_plan_repository(request: Request) -> InMemoryPlanRepository:
    return request.app.state.plan_repository


def get_proposal_repository(request: Request) -> InMemoryAIProposalRepository:
    return request.app.state.proposal_repository


def get_music_repository(request: Request) -> InMemoryMusicRepository:
    return request.app.state.music_repository


def get_effects_repository(request: Request) -> InMemoryEffectsRepository:
    return request.app.state.effects_repository


def get_broll_repository(request: Request) -> InMemoryBrollRepository:
    return request.app.state.broll_repository


def get_billing_repository(request: Request) -> InMemoryBillingRepository:
    return request.app.state.billing_repository


def get_analytics_repository(request: Request) -> InMemoryAnalyticsRepository:
    return request.app.state.analytics_repository


def get_workflow_repository(
    request: Request,
) -> InMemoryWorkflowRepository | SupabaseWorkflowRepository:
    return request.app.state.workflow_repository


def get_visual_repository(request: Request) -> InMemoryVisualRepository:
    return request.app.state.visual_repository


def get_payment_gateway(request: Request) -> PaymentGateway:
    return request.app.state.payment_gateway


def get_music_providers(request: Request) -> list[MusicProvider]:
    return request.app.state.music_providers


def get_music_queue(request: Request) -> MusicQueue:
    return request.app.state.music_queue


def get_effects_queue(request: Request) -> EffectsQueue:
    return request.app.state.effects_queue


def get_regeneration_queue(request: Request) -> RegenerationQueue:
    return request.app.state.regeneration_queue


def get_transcription_provider(request: Request) -> TranscriptionProvider:
    return request.app.state.transcription_provider


def get_visual_intelligence_provider(request: Request) -> VisualIntelligenceProvider:
    return request.app.state.visual_intelligence_provider


def get_edit_planner_provider(request: Request) -> EditPlannerProvider:
    return request.app.state.edit_planner_provider


def get_stock_video_providers(request: Request) -> list[StockVideoProvider]:
    return request.app.state.stock_video_providers
