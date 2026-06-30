from uuid import UUID

from fastapi import Request

from app.api.billing import record_render_success_usage
from app.repositories.renders import RenderJobRecord
from app.schemas.billing import BillingPlanKey
from app.schemas.renders import RenderJobStatus
from app.services.ai_feedback import record_render_kept_signals


def record_render_success_outcome(*, request: Request, job: RenderJobRecord) -> None:
    if job.status != RenderJobStatus.COMPLETE:
        return
    manifest = job.manifest if isinstance(job.manifest, dict) else {}
    user_id_value = manifest.get("creator_user_id")
    if user_id_value:
        record_render_kept_signals(
            request=request,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            user_id=UUID(str(user_id_value)),
            edit_plan_id=UUID(str(manifest["edit_plan_id"]))
            if manifest.get("edit_plan_id")
            else None,
            render_job_id=job.id,
            render_props=manifest.get("render_props")
            if isinstance(manifest.get("render_props"), dict)
            else {},
        )
    settings = request.app.state.settings
    repository = getattr(request.app.state, "operations_repository", None)
    if repository is not None:
        _record_render_costs(request=request, job=job, manifest=manifest, repository=repository)
    if settings.billing_enabled:
        record_render_success_usage(
            workspace_id=job.workspace_id,
            render_job_id=job.id,
            repository=request.app.state.billing_repository,
            plan=BillingPlanKey(str(manifest.get("billing_plan") or "free")),
        )


def _record_render_costs(*, request, job, manifest, repository) -> None:
    compute_category = (
        "compute_gpu" if str(manifest.get("compute_tier") or "cpu") == "gpu" else "compute_cpu"
    )
    repository.record_cost(
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        render_job_id=job.id,
        provider=str(getattr(job.render_engine, "value", None) or "ffmpeg"),
        category=compute_category,
        quantity=float(job.benchmark_ms or 0) / 1000,
        unit="second",
        unit_cost_usd=float(manifest.get("compute_unit_cost_usd") or 0),
        metadata={"renderer_version": job.renderer_version},
    )
    exports = request.app.state.render_repository.list_exports(
        project_id=job.project_id, workspace_id=job.workspace_id
    )
    export = next((item for item in reversed(exports) if item.render_job_id == job.id), None)
    if export:
        repository.record_cost(
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            render_job_id=job.id,
            provider="supabase_storage",
            category="storage",
            quantity=float(export.file_size_bytes or 0) / 1_000_000_000,
            unit="gb_month",
            unit_cost_usd=float(manifest.get("storage_unit_cost_usd") or 0.021),
            metadata={"export_id": str(export.id)},
        )
