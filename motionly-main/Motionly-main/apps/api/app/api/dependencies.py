import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, Request

from app.api.errors import ApiError
from app.core.config import LOCAL_AUTH_BYPASS_ENVS, env_flag_enabled
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.effects import InMemoryEffectsRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.projects import InMemoryProjectRepository
from app.repositories.publishing import InMemoryPublishingRepository
from app.repositories.regeneration import InMemoryRegenerationRepository
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.services.auth import (
    require_workspace_membership,
    verify_supabase_jwt,
    verify_supabase_token_with_auth_api,
)
from app.services.billing import StripeBillingClient
from app.services.edit_planner import EditPlannerProvider
from app.services.music import MusicProvider
from app.services.offload import run_blocking
from app.services.queue import (
    BrollQueue,
    EffectsQueue,
    MatteQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    PublishQueue,
    RegenerationQueue,
    RenderQueue,
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


async def get_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_motionly_user_id: str | None = Header(default=None),
    x_motionly_workspace_id: str | None = Header(default=None),
) -> Actor:
    settings = request.app.state.settings
    if env_flag_enabled(os.getenv("SKIP_API_AUTH")):
        if settings.app_env not in LOCAL_AUTH_BYPASS_ENVS:
            raise ApiError(500, "auth_not_configured", "API authentication is not configured.")
        return Actor(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        )

    if not settings.supabase_jwt_secret:
        raise ApiError(500, "auth_not_configured", "API authentication is not configured.")

    if settings.supabase_jwt_secret:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "auth_required", "Sign in before using Motionly.")
        token = authorization.split(" ", 1)[1].strip()
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
        if settings.supabase_url and settings.supabase_service_role_key:
            await run_blocking(
                require_workspace_membership,
                supabase_url=str(settings.supabase_url),
                service_role_key=settings.supabase_service_role_key,
                user_id=verified.user_id,
                workspace_id=workspace_id,
                metrics_registry=getattr(request.app.state, "metrics_registry", None),
            )
        elif settings.app_env not in LOCAL_AUTH_BYPASS_ENVS:
            raise ApiError(500, "auth_not_configured", "Workspace auth is not configured.")
        return Actor(user_id=verified.user_id, workspace_id=workspace_id)


def get_upload_repository(request: Request) -> InMemoryUploadRepository:
    return request.app.state.upload_repository


def get_project_repository(request: Request) -> InMemoryProjectRepository:
    return request.app.state.project_repository


def get_storage_signer(request: Request) -> StorageSigner:
    return request.app.state.storage_signer


def get_preprocess_queue(request: Request) -> PreprocessQueue:
    return request.app.state.preprocess_queue


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


def get_render_queue(request: Request) -> RenderQueue:
    return request.app.state.render_queue


def get_publish_queue(request: Request) -> PublishQueue:
    return request.app.state.publish_queue


def get_publishing_repository(request: Request) -> InMemoryPublishingRepository:
    return request.app.state.publishing_repository


def get_caption_repository(request: Request) -> InMemoryCaptionRepository:
    return request.app.state.caption_repository


def get_render_repository(request: Request) -> InMemoryRenderRepository:
    return request.app.state.render_repository


def get_regeneration_repository(request: Request) -> InMemoryRegenerationRepository:
    return request.app.state.regeneration_repository


def get_plan_repository(request: Request) -> InMemoryPlanRepository:
    return request.app.state.plan_repository


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


def get_visual_repository(request: Request) -> InMemoryVisualRepository:
    return request.app.state.visual_repository


def get_stripe_billing_client(request: Request) -> StripeBillingClient:
    return request.app.state.stripe_billing_client


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
