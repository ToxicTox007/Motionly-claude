import json
from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.core.security import constant_time_token_matches
from app.schemas.renders import RenderJobStatus
from app.services.matte_jobs import run_matte_for_upload
from app.services.outcomes import record_render_success_outcome
from app.services.publishing import execute_publish_job
from app.services.queue import BullMQQueueRegistry, EnqueuedJob, QueueJobStatus, QueueRegistry
from app.services.render_jobs import complete_render_job_by_id
from app.services.scale import execute_scale_resource
from app.services.transcription_jobs import (
    queue_transcription_for_upload,
    run_transcription_for_upload,
)
from app.services.upload_jobs import complete_upload_preprocessing_by_id
from app.services.visual_intelligence import run_visual_analysis_for_upload
from app.services.visual_jobs import (
    build_visual_cache_key,
    queue_visual_analysis_for_upload,
    visual_provider_identity,
)

router = APIRouter(prefix="/internal/jobs", tags=["internal jobs"])


class WorkerJobRunResponse(BaseModel):
    queue_job_id: str
    kind: str
    status: QueueJobStatus
    result: dict[str, Any] = Field(default_factory=dict)


async def require_worker_token(
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> None:
    settings = request.app.state.settings
    if not settings.worker_api_token:
        raise ApiError(500, "worker_token_not_configured", "Worker token is not configured.")
    if not constant_time_token_matches(x_motionly_worker_token, settings.worker_api_token):
        raise ApiError(403, "worker_access_denied", "Worker access is required.")


@router.post("/{queue_job_id}/run", response_model=WorkerJobRunResponse)
async def run_queue_job(
    queue_job_id: str,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> WorkerJobRunResponse:
    await require_worker_token(request, x_motionly_worker_token)
    if getattr(request.app.state.settings, "app_env", "test") == "production":
        raise ApiError(
            410,
            "direct_worker_execution_required",
            "Media jobs execute from BullMQ workers and cannot run through the API process.",
        )
    registry: QueueRegistry = request.app.state.queue_registry
    if isinstance(registry, BullMQQueueRegistry):
        raise ApiError(
            410,
            "direct_worker_execution_required",
            "BullMQ jobs execute in dedicated workers and cannot run through the API process.",
        )
    try:
        job = registry.jobs[queue_job_id]
    except KeyError as exc:
        raise ApiError(404, "queue_job_not_found", "Queue job was not found.") from exc

    if job.status == QueueJobStatus.SUCCEEDED:
        return _response(job, result={"message": "Job already succeeded."})
    if job.status in {QueueJobStatus.CANCELED, QueueJobStatus.DEAD_LETTER}:
        raise ApiError(409, "queue_job_not_runnable", "Queue job can no longer be run.")
    if job.status == QueueJobStatus.RUNNING and not isinstance(registry, BullMQQueueRegistry):
        raise ApiError(409, "queue_job_already_running", "Queue job is already running.")

    if job.status != QueueJobStatus.RUNNING:
        job = registry.start(queue_job_id)

    from app.services.operations import monotonic_ms

    metrics = getattr(request.app.state, "metrics_registry", None)
    if metrics:
        metrics.observe("job_retry_count", job.attempts, kind=job.kind)

    started_ms = monotonic_ms()
    try:
        if job.kind == "batch_plan" and job.workspace_id and job.resource_id:
            from app.api.internal_batches import run_batch

            result = await run_batch(
                workspace_id=job.workspace_id,
                batch_id=job.resource_id,
                request=request,
                x_motionly_worker_token=x_motionly_worker_token,
            )
        else:
            result = _execute_job(job=job, request=request)

        latency = monotonic_ms() - started_ms
        status = "failed" if result.get("failed") else "succeeded"
        if metrics:
            metrics.observe("job_duration_ms", latency, kind=job.kind, status=status)
            if status == "failed":
                metrics.observe(
                    "job_failure_count",
                    1,
                    kind=job.kind,
                    error_code=str(result.get("error_code") or "job_failed"),
                )
    except Exception as exc:
        latency = monotonic_ms() - started_ms
        error_code = exc.code if isinstance(exc, ApiError) else type(exc).__name__
        if metrics:
            metrics.observe("job_duration_ms", latency, kind=job.kind, status="failed")
            metrics.observe(
                "job_failure_count",
                1,
                kind=job.kind,
                error_code=error_code,
            )
            if error_code == "cuda_oom":
                metrics.observe("cuda_oom_count", 1, kind=job.kind)
        backoff_seconds = min(300, 2 ** max(0, job.attempts) * 5)
        registry.fail(
            queue_job_id,
            error_code=error_code,
            error_message=_safe_job_error_message(exc),
            backoff_seconds=backoff_seconds,
        )
        raise

    if result.get("failed"):
        error_code = str(result.get("error_code") or "job_failed")
        failed = registry.fail(
            queue_job_id,
            error_code=error_code,
            error_message=str(result.get("error_message") or "Job failed."),
            backoff_seconds=min(300, 2 ** max(0, job.attempts) * 5),
        )
        return _response(failed, result=result)

    completed = registry.complete(queue_job_id)
    return _response(completed, result=result)


def _execute_job(*, job: EnqueuedJob, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not job.workspace_id or not job.project_id or not job.resource_id:
        raise ApiError(422, "queue_job_payload_invalid", "Queue job is missing resource context.")

    if job.kind == "video_preprocess":
        upload = complete_upload_preprocessing_by_id(
            repository=request.app.state.upload_repository,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
        )
        result = {
            "upload_id": str(upload.id),
            "upload_status": upload.status.value,
        }
        if job.workflow_run_id is not None:
            return result
        if all(
            hasattr(request.app.state, name)
            for name in (
                "caption_repository",
                "transcription_queue",
                "transcription_provider",
            )
        ):
            transcript = queue_transcription_for_upload(
                upload=upload,
                caption_repository=request.app.state.caption_repository,
                transcription_queue=request.app.state.transcription_queue,
                transcription_provider=request.app.state.transcription_provider,
            )
            result.update(
                {
                    "transcript_id": str(transcript.id),
                    "transcript_status": transcript.status.value,
                }
            )
        if all(hasattr(request.app.state, name) for name in ("visual_repository", "visual_queue")):
            provider_name, model = visual_provider_identity(settings)
            cache_key = build_visual_cache_key(
                upload=upload,
                provider=provider_name,
                model=model,
                sample_rate_fps=settings.visual_sample_fps,
                scene_threshold=settings.visual_scene_threshold,
            )
            scheduled = queue_visual_analysis_for_upload(
                upload=upload,
                repository=request.app.state.visual_repository,
                visual_queue=request.app.state.visual_queue,
                provider=provider_name,
                model=model,
                cache_key=cache_key,
            )
            result.update(
                {
                    "visual_manifest_id": str(scheduled.manifest.id),
                    "visual_status": scheduled.manifest.status.value,
                    "visual_job_id": scheduled.job.id if scheduled.job else None,
                }
            )
        return result

    if job.kind == "transcription":
        transcript = run_transcription_for_upload(
            upload_repository=request.app.state.upload_repository,
            caption_repository=request.app.state.caption_repository,
            transcription_provider=request.app.state.transcription_provider,
            audio_ml_providers=getattr(request.app.state, "audio_ml_providers", None),
            audio_intelligence_provider=getattr(
                request.app.state,
                "audio_intelligence_provider",
                None,
            ),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            retain_debug_audio=settings.transcription_debug_retain_audio,
        )
        return {
            "transcript_id": str(transcript.id),
            "transcript_status": transcript.status.value,
            "quality_status": transcript.quality_status.value,
            "quality_reasons": transcript.quality_reasons,
            "error_code": transcript.error_code,
            "error_message": transcript.error_message,
            "failed": transcript.status.value == "failed",
        }

    if job.kind == "visual_analysis":
        provider = request.app.state.visual_intelligence_provider
        if getattr(provider, "provider_name", "") != "fixture_visual":
            raise ApiError(
                409,
                "visual_gpu_worker_required",
                "Production visual inference must run in the dedicated GPU/ML worker.",
            )
        record = run_visual_analysis_for_upload(
            repository=request.app.state.visual_repository,
            provider=provider,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            sample_rate_fps=settings.visual_sample_fps,
            scene_threshold=settings.visual_scene_threshold,
        )
        return {
            "video_manifest_id": str(record.id),
            "visual_status": record.status.value,
            "scene_count": len(record.manifest.scenes) if record.manifest else 0,
            "failed": record.status.value == "failed",
        }

    if job.kind == "matte_extraction":
        from uuid import UUID

        record = run_matte_for_upload(
            repository=request.app.state.visual_repository,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            matte_id=UUID(job.render_engine) if job.render_engine else None,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            provider_models=request.app.state.matte_provider_models,
            model_gateway=getattr(request.app.state, "model_gateway", None),
            matte_gateway_route=getattr(request.app.state, "matte_gateway_route", None),
            production=getattr(settings, "app_env", "test") == "production",
        )
        return {
            "matte_id": str(record.id),
            "matte_status": record.status.value,
            "cache_key": record.cache_key,
            "failed": record.status.value == "failed",
            "error_code": record.error_code,
            "error_message": record.error_message,
        }

    if job.kind == "render":
        render = complete_render_job_by_id(
            repository=request.app.state.render_repository,
            metrics_registry=getattr(request.app.state, "metrics_registry", None),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            render_job_id=job.resource_id,
            export_bucket=settings.export_storage_bucket,
            storage_root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            execute_ffmpeg=settings.render_execute_ffmpeg,
            allow_fixture_media=(
                getattr(settings, "app_env", "test") != "production"
                or getattr(settings, "allow_fixture_providers", False)
            ),
        )
        record_render_success_outcome(request=request, job=render)
        return {
            "render_job_id": str(render.id),
            "render_status": render.status.value,
            "failed": render.status == RenderJobStatus.FAILED,
        }

    if job.kind == "publish":
        published = execute_publish_job(
            publishing_repository=request.app.state.publishing_repository,
            render_repository=request.app.state.render_repository,
            storage_signer=request.app.state.storage_signer,
            client=request.app.state.social_publishing_client,
            oauth_client=request.app.state.publish_oauth_client,
            settings=settings,
            job_id=job.resource_id,
            workspace_id=job.workspace_id,
        )
        if published.status.value == "published":
            export = request.app.state.render_repository.get_export(
                export_id=published.export_id, workspace_id=job.workspace_id
            )
            request.app.state.operations_repository.approve_export(
                workspace_id=job.workspace_id,
                render_job_id=export.render_job_id,
                export_id=export.id,
            )
        return {
            "publish_job_id": str(published.id),
            "publish_status": published.status.value,
            "external_post_id": published.external_post_id,
            "failed": published.status.value == "failed",
        }

    if job.kind == "phase5":
        resource = execute_scale_resource(
            repository=request.app.state.scale_repository,
            resource_id=job.resource_id,
            workspace_id=job.workspace_id,
            settings=settings,
            upload_repository=request.app.state.upload_repository,
            broll_repository=request.app.state.broll_repository,
            visual_provider=(
                getattr(request.app.state, "visual_gateway_provider", None)
                or request.app.state.visual_intelligence_provider
            ),
            analytics_repository=request.app.state.analytics_repository,
            stem_separator=request.app.state.stem_separator,
            runway_provider=request.app.state.runway_provider,
            model_gateway=getattr(request.app.state, "model_gateway", None),
            runway_gateway_route=getattr(
                request.app.state,
                "runway_gateway_route",
                None,
            ),
            stem_gateway_route=getattr(
                request.app.state,
                "stem_gateway_route",
                None,
            ),
            operations_repository=request.app.state.operations_repository,
            billing_repository=request.app.state.billing_repository,
            editorial_learning_repository=getattr(
                request.app.state, "editorial_learning_repository", None
            ),
            object_reference_service=getattr(request.app.state, "object_reference_service", None),
        )
        return {
            "resource_id": str(resource.id),
            "resource_kind": resource.kind,
            "resource_status": resource.status.value,
            "failed": resource.status.value == "failed",
        }

    if job.kind == "edit_plan":
        from app.services.proposal_generation import execute_proposal_generation

        max_paid_attempts = max(1, int(getattr(settings, "max_paid_provider_attempts", 1)))
        if job.attempts >= max_paid_attempts:
            raise ApiError(
                429,
                "edit_planner_retry_limit_exceeded",
                "Paid edit-planner retry limit reached.",
            )

        plan_repository = request.app.state.plan_repository
        proposal_repository = getattr(request.app.state, "proposal_repository", None) or getattr(
            plan_repository, "_proposal_repository", None
        )
        if proposal_repository is None:
            raise ApiError(
                500,
                "proposal_repository_missing",
                "Proposal storage is required for edit planning.",
            )
        result = execute_proposal_generation(
            proposal_repository=proposal_repository,
            plan_repository=plan_repository,
            planner_provider=request.app.state.edit_planner_provider,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            proposal_id=job.resource_id,
            settings=settings,
            billing_repository=getattr(request.app.state, "billing_repository", None),
            operations_repository=getattr(request.app.state, "operations_repository", None),
            metrics_registry=getattr(request.app.state, "metrics_registry", None),
        )
        if job.workflow_run_id is not None:
            from app.schemas.ai_proposals import ProposalState

            canonical = proposal_repository.get(
                proposal_id=job.resource_id,
                project_id=job.project_id,
                workspace_id=job.workspace_id,
            )
            if (
                canonical.workflow_run_id != job.workflow_run_id
                or canonical.state != ProposalState.AWAITING_APPROVAL
                or result.get("status") != ProposalState.AWAITING_APPROVAL.value
            ):
                return {
                    "proposal_id": str(canonical.id),
                    "status": canonical.state.value,
                    "failed": True,
                    "error_code": "workflow_proposal_not_approval_ready",
                    "error_message": "Canonical workflow proposal is not awaiting approval.",
                }
        return result

    if job.kind == "broll_generation":
        from uuid import UUID

        from app.schemas.broll import BrollGenerateRequest
        from app.services.layer_jobs import execute_broll_generation, parse_layer_job_payload

        params = parse_layer_job_payload(job.render_engine)
        payload = BrollGenerateRequest.model_validate(params["payload"])
        user_id = UUID(params["user_id"]) if params.get("user_id") else None
        return execute_broll_generation(
            repository=request.app.state.broll_repository,
            billing_repository=request.app.state.billing_repository,
            providers=request.app.state.stock_video_providers,
            storage_signer=request.app.state.storage_signer,
            stock_asset_enrichment_queue=request.app.state.stock_asset_enrichment_queue,
            settings=settings,
            embedding_provider=getattr(
                request.app.state,
                "embedding_gateway_provider",
                None,
            ),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=user_id,
            payload=payload,
        )

    if job.kind == "stock_asset_enrichment":
        from app.services.stock_video import enrich_stock_asset

        params = json.loads(job.render_engine or "{}")
        clip_model_name = str(
            params.get("clip_model_name")
            or getattr(settings, "visual_clip_model", "openai/clip-vit-base-patch32")
        )
        asset = request.app.state.broll_repository.get_asset(
            project_id=job.project_id,
            workspace_id=job.workspace_id,
            asset_id=job.resource_id,
        )
        asset = enrich_stock_asset(
            asset=asset,
            repository=request.app.state.broll_repository,
            clip_model_name=clip_model_name,
            embedding_provider=getattr(
                request.app.state,
                "embedding_gateway_provider",
                None,
            ),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
        )
        return {
            "asset_id": str(asset.id),
            "status": asset.raw_metadata.get("enrichment_status"),
        }

    if job.kind == "music_selection":
        from uuid import UUID

        from app.schemas.music import MusicSelectRequest
        from app.services.layer_jobs import execute_music_selection, parse_layer_job_payload

        params = parse_layer_job_payload(job.render_engine)
        payload = MusicSelectRequest.model_validate(params["payload"])
        user_id = UUID(params["user_id"]) if params.get("user_id") else None
        return execute_music_selection(
            repository=request.app.state.music_repository,
            plan_repository=request.app.state.plan_repository,
            providers=request.app.state.music_providers,
            request=request,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=user_id,
            payload=payload,
        )

    if job.kind == "effects_config":
        from uuid import UUID

        from app.schemas.effects import EffectsConfigRequest
        from app.services.layer_jobs import execute_effects_update, parse_layer_job_payload

        params = parse_layer_job_payload(job.render_engine)
        payload = EffectsConfigRequest.model_validate(params["payload"])
        user_id = UUID(params["user_id"]) if params.get("user_id") else None
        return execute_effects_update(
            repository=request.app.state.effects_repository,
            billing_repository=request.app.state.billing_repository,
            plan_repository=request.app.state.plan_repository,
            request=request,
            settings=settings,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=user_id,
            payload=payload,
        )

    if job.kind == "caption_generation":
        from app.schemas.captions import CaptionGenerateRequest
        from app.services.layer_jobs import execute_caption_generation, parse_layer_job_payload

        params = parse_layer_job_payload(job.render_engine)
        payload = CaptionGenerateRequest.model_validate(params["payload"])
        return execute_caption_generation(
            repository=request.app.state.caption_repository,
            settings=settings,
            model_gateway=getattr(request.app.state, "model_gateway", None),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            payload=payload,
        )

    if job.kind == "layer_regeneration":
        from app.schemas.regeneration import RegenerateRequest
        from app.services.layer_jobs import execute_regeneration, parse_layer_job_payload

        params = parse_layer_job_payload(job.render_engine)
        payload = RegenerateRequest.model_validate(params["payload"])
        return execute_regeneration(
            repository=request.app.state.regeneration_repository,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            payload=payload,
            control_settings_id=job.resource_id,
        )

    return {
        "message": f"Unhandled worker job kind: {job.kind}",
        "skipped": True,
        "failed": True,
    }


def _safe_job_error_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.message
    return "Job failed. Retry if the failure is transient."


def _response(job: EnqueuedJob, *, result: dict[str, Any]) -> WorkerJobRunResponse:
    return WorkerJobRunResponse(
        queue_job_id=job.id,
        kind=job.kind,
        status=job.status,
        result=result,
    )
