from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.schemas.renders import RenderJobStatus
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
from app.services.visual_jobs import queue_visual_analysis_for_upload, visual_provider_identity

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
    if not settings.admin_api_token:
        raise ApiError(500, "worker_token_not_configured", "Worker token is not configured.")
    if x_motionly_worker_token != settings.admin_api_token:
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
    except Exception:
        registry.fail(queue_job_id)
        raise

    if result.get("failed"):
        failed = registry.fail(queue_job_id)
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
            scheduled = queue_visual_analysis_for_upload(
                upload=upload,
                repository=request.app.state.visual_repository,
                visual_queue=request.app.state.visual_queue,
                provider=provider_name,
                model=model,
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

    if job.kind == "render":
        render = complete_render_job_by_id(
            repository=request.app.state.render_repository,
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
            visual_provider=request.app.state.visual_intelligence_provider,
            analytics_repository=request.app.state.analytics_repository,
            stem_separator=request.app.state.stem_separator,
            runway_provider=request.app.state.runway_provider,
            operations_repository=request.app.state.operations_repository,
        )
        return {
            "resource_id": str(resource.id),
            "resource_kind": resource.kind,
            "resource_status": resource.status.value,
            "failed": resource.status.value == "failed",
        }

    if job.kind == "edit_plan":
        import json
        from uuid import UUID
        from app.schemas.captions import TranscriptStatus
        from app.schemas.plans import (
            EDIT_PLAN_SCHEMA_VERSION,
            EditPlanStatus,
            EditPlanStyle,
            BrollDensity,
            EditPlan,
            PlanSummary,
            EditPlanCut,
        )
        from app.services.style_classifier import classify_edit_style
        from app.services.edit_planner import (
            PLANNER_SCHEMA_HASH,
            build_plan_cache_key,
            normalize_plan,
            summarize_plan,
        )
        from app.services.timeline_compiler import migrate_legacy_plan_to_timeline
        from app.services.operations import monotonic_ms
        from app.services.ai_feedback import record_plan_suggestions

        repository = request.app.state.plan_repository
        planner_provider = request.app.state.edit_planner_provider
        operations = getattr(request.app.state, "operations_repository", None)

        params = json.loads(job.render_engine)
        prompt = params["prompt"]
        style = EditPlanStyle(params["style"]) if params.get("style") else None
        target_duration_s = int(params["target_duration_s"])
        broll_density = BrollDensity(params["broll_density"])
        feedback = params["feedback"]
        style_profile_id = UUID(params["style_profile_id"]) if params.get("style_profile_id") else None
        saved_template_id = UUID(params["saved_template_id"]) if params.get("saved_template_id") else None
        user_id = UUID(params["user_id"]) if params.get("user_id") else None

        repository.get_project_for_workspace(project_id=job.project_id, workspace_id=job.workspace_id)
        transcript = repository.latest_transcript(
            project_id=job.project_id,
            workspace_id=job.workspace_id,
        )
        if not transcript:
            raise ApiError(404, "transcript_not_found", "Generate a transcript before planning edits.")
        if transcript.status != TranscriptStatus.READY:
            raise ApiError(
                409,
                str(transcript.status),
                transcript.error_message or "Transcript is not ready for edit planning.",
            )

        upload = repository.get_active_upload(project_id=job.project_id, workspace_id=job.workspace_id)
        video_duration_ms = upload.metadata.duration_ms or transcript.duration_ms or 0
        
        visual_manifest = None
        visual_context_flag = "visual_context_unavailable"
        visual_repository = getattr(repository, "visual_repository", None)
        if visual_repository is not None:
            latest = visual_repository.latest_for_upload(
                uploaded_video_id=upload.id,
                project_id=job.project_id,
                workspace_id=job.workspace_id,
            )
            record_ready = visual_repository.latest_ready_for_upload(
                uploaded_video_id=upload.id,
                project_id=job.project_id,
                workspace_id=job.workspace_id,
            )
            if record_ready and record_ready.manifest:
                visual_manifest = record_ready.manifest
                visual_context_flag = None
            else:
                status = latest.status.value if latest else "unavailable"
                visual_context_flag = f"visual_context_{status}"

        style_classification = classify_edit_style(
            transcript=transcript,
            visual_manifest=visual_manifest,
            requested_style=style,
            prompt=prompt,
            feedback=feedback,
        )
        
        from app.schemas.plans import PlanCreateRequest
        effective_payload = PlanCreateRequest(
            prompt=prompt,
            style=style_classification.effective_style,
            target_duration_s=target_duration_s,
            broll_density=broll_density,
            feedback=feedback,
            style_profile_id=style_profile_id,
            saved_template_id=saved_template_id,
        )
        
        cache_key = build_plan_cache_key(
            transcript=transcript,
            payload=effective_payload,
            visual_manifest=visual_manifest,
            style_classification=style_classification,
            model=getattr(planner_provider, "model", None),
            provider=getattr(planner_provider, "provider_name", None),
        )

        started_ms = monotonic_ms()
        try:
            result = planner_provider.create_plan(
                transcript=transcript,
                payload=effective_payload,
                video_duration_ms=video_duration_ms,
                visual_manifest=visual_manifest,
                style_classification=style_classification,
            )
        except Exception as exc:
            if operations is not None:
                operations.record_provider_slo(
                    workspace_id=job.workspace_id,
                    project_id=job.project_id,
                    provider=planner_provider.provider_name,
                    operation="edit_plan",
                    success=False,
                    latency_ms=monotonic_ms() - started_ms,
                    error_code=type(exc).__name__,
                )
            repository.create_plan(
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                transcript_id=transcript.id,
                schema_version=EDIT_PLAN_SCHEMA_VERSION,
                status=EditPlanStatus.FAILED,
                style=style_classification.effective_style,
                target_duration_s=target_duration_s,
                broll_density=broll_density,
                prompt=prompt.strip(),
                feedback=feedback.strip() if feedback else None,
                provider=planner_provider.provider_name,
                model="failed_generation",
                cache_key=cache_key,
                schema_hash=PLANNER_SCHEMA_HASH,
                latency_ms=monotonic_ms() - started_ms,
                cost_estimate_usd=0.0,
                validation_errors=[str(exc)],
                summary=PlanSummary(
                    duration_s=target_duration_s,
                    cut_count=0,
                    broll_count=0,
                    zoom_count=0,
                    music_mood="failed",
                    effects=[],
                    color_grade="failed",
                    risk_flags=[visual_context_flag] if visual_context_flag else [],
                ),
                plan=EditPlan(
                    style=style_classification.effective_style,
                    target_duration_s=target_duration_s,
                    cuts=[EditPlanCut(start_ms=0, end_ms=1000, reason="failed")],
                    music_mood="failed",
                    color_grade="failed"
                ),
            )
            raise

        if operations is not None:
            operations.record_provider_slo(
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                provider=result.provider,
                operation="edit_plan",
                success=True,
                latency_ms=result.latency_ms,
                error_code=None,
            )

        plan = normalize_plan(
            plan=result.plan,
            words=transcript.words,
            duration_ms=video_duration_ms or result.plan.target_duration_s * 1000,
        )
        if visual_context_flag and visual_context_flag not in plan.risk_flags:
            plan = plan.model_copy(update={"risk_flags": [*plan.risk_flags, visual_context_flag][:10]})
        plan = migrate_legacy_plan_to_timeline(
            plan=plan,
            source_video_id=upload.id,
            source_duration_ms=video_duration_ms or result.plan.target_duration_s * 1000,
        )
        record = repository.create_plan(
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            transcript_id=transcript.id,
            schema_version=EDIT_PLAN_SCHEMA_VERSION,
            status=EditPlanStatus.READY,
            style=style_classification.effective_style,
            target_duration_s=effective_payload.target_duration_s,
            broll_density=effective_payload.broll_density,
            prompt=effective_payload.prompt.strip(),
            feedback=effective_payload.feedback.strip() if effective_payload.feedback else None,
            provider=result.provider,
            model=result.model,
            cache_key=cache_key,
            schema_hash=PLANNER_SCHEMA_HASH,
            latency_ms=result.latency_ms,
            cost_estimate_usd=result.cost_estimate_usd,
            validation_errors=result.validation_errors,
            summary=summarize_plan(plan, style_classification=style_classification),
            plan=plan,
        )

        if operations is not None:
            usage = result.raw_payload.get("usage") if isinstance(result.raw_payload, dict) else {}
            operations.record_cost(
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                provider=result.provider,
                category="tokens",
                quantity=float(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0,
                unit="token",
                unit_cost_usd=(
                    float(result.cost_estimate_usd or 0)
                    / float(usage.get("total_tokens") or 1)
                    if isinstance(usage, dict)
                    else 0
                ),
                metadata={"model": result.model, "plan_id": str(record.id)},
            )

        record_plan_suggestions(
            request=request,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=user_id,
            plan=record,
        )

        return {
            "plan_id": str(record.id),
            "status": "ready",
        }

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
            settings=settings,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=user_id,
            payload=payload,
        )

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



def _response(job: EnqueuedJob, *, result: dict[str, Any]) -> WorkerJobRunResponse:
    return WorkerJobRunResponse(
        queue_job_id=job.id,
        kind=job.kind,
        status=job.status,
        result=result,
    )
