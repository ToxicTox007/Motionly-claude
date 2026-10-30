import argparse
import json
import logging
import sys
from uuid import UUID

from app.core.config import get_settings
from app.core.production import validate_production_settings
from app.repositories.billing import SupabaseBillingRepository
from app.repositories.uploads import SupabaseUploadRepository
from app.repositories.visual import SupabaseVisualRepository
from app.services.billing import PlanFeatureQuotaGuard
from app.services.inference_runtime import build_inference_runtime
from app.services.model_choice import wire_worker_choice_resolver
from app.services.model_domain_adapters import (
    GatewayVisualIntelligenceProvider,
    bundle_revision,
    register_visual_route,
)
from app.services.model_gateway import ModelOperation, validate_gateway_operations
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.remote_visual import remote_visual_provider_from_settings
from app.services.visual_intelligence import LocalMLVisualIntelligenceProvider
from app.services.visual_jobs import local_visual_model_identity
from app.workers.executor_protocol import error_reply, open_protocol_channel
from app.workers.inference_fingerprint import (
    VISUAL_CAPABILITY_IDS,
    enforce_registry_fingerprint,
)

logger = logging.getLogger(__name__)


def main() -> None:
    if "--serve" in sys.argv:
        _serve()
        return
    args = _arguments()
    result = _runner()(vars(args))
    print(json.dumps(result))


def _runner():
    settings = get_settings()
    validate_production_settings(settings)
    if (
        settings.ml_execution_mode == "test_fixture"
        and settings.visual_intelligence_provider.strip().lower() != "fixture"
    ):
        raise RuntimeError(
            "ML_EXECUTION_MODE=test_fixture requires VISUAL_PROVIDER=fixture; "
            "real models run only on the GPU-stage workers"
        )
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    upload_repository = SupabaseUploadRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    repository = SupabaseVisualRepository(
        upload_repository=upload_repository,
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    visual_provider_key = settings.visual_intelligence_provider.strip().lower()
    operations = OperationsRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    runtime = build_inference_runtime(
        settings,
        metrics_registry=MetricsRegistry(),
        operations_repository=operations,
    )
    gateway = runtime.gateway
    if gateway is None:
        raise RuntimeError("Model gateway is required for visual inference")
    # Per-plan quotas for the remote keyframe enrichers; the local GPU manifest
    # route has no metered budget_feature and is unaffected.
    gateway.set_quota_guard(
        PlanFeatureQuotaGuard(
            billing_repository=SupabaseBillingRepository(
                supabase_url=str(settings.supabase_url),
                service_role_key=settings.supabase_service_role_key,
            ),
            operations_repository=operations,
            settings=settings,
        )
    )
    # Background jobs follow the user's model choice too (plan Section 2.11,
    # Task 31): wire the resolver on this worker's gateway from the shared runtime.
    wire_worker_choice_resolver(gateway, runtime=runtime, settings=settings)
    if visual_provider_key == "fixture":
        from app.services.visual_intelligence import FixtureVisualIntelligenceProvider

        native_provider = FixtureVisualIntelligenceProvider()
        revision = bundle_revision(native_provider.model)
    elif visual_provider_key == "remote":
        # CPU signals plus remote keyframe enrichment: no GPU model is loaded.
        native_provider = remote_visual_provider_from_settings(settings, gateway=gateway)
        revision = bundle_revision(native_provider.model)
    else:
        # Local CLIP/BLIP/YOLO identity comes from the registry local routes
        # (gpu-local.json, plan Task 14), never a config model-id default.
        identity = local_visual_model_identity(gateway)
        native_provider = LocalMLVisualIntelligenceProvider(
            clip_model=identity["clip_model"],
            blip_model=identity["blip_model"],
            yolo_model=identity["yolo_model"],
            clip_revision=identity["clip_revision"],
            blip_revision=identity["blip_revision"],
            yolo_revision=identity["yolo_revision"],
        )
        revision = bundle_revision(
            identity["clip_revision"],
            identity["blip_revision"],
            identity["yolo_revision"],
        )
    route = register_visual_route(
        gateway,
        provider=native_provider,
        model=native_provider.model,
        revision=revision,
    )
    if settings.app_env == "production":
        validate_gateway_operations(
            gateway,
            required=(ModelOperation.VISION_VIDEO_MANIFEST,),
            runtime="visual worker",
        )
    provider = GatewayVisualIntelligenceProvider(gateway=gateway, route=route)
    from app.services.visual_intelligence import run_visual_analysis_for_upload

    def execute(payload: dict[str, object]) -> dict[str, object]:
        # Fail closed if the API enqueued this job against a different inference
        # catalog for the capabilities visual analysis requires (plan Task 6).
        enforce_registry_fingerprint(
            payload, runtime=runtime, capability_ids=VISUAL_CAPABILITY_IDS
        )
        record = run_visual_analysis_for_upload(
            repository=repository,
            provider=provider,
            workspace_id=UUID(str(payload["workspace_id"])),
            project_id=UUID(str(payload["project_id"])),
            video_id=UUID(str(payload["video_id"])),
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            sample_rate_fps=settings.visual_sample_fps,
            scene_threshold=settings.visual_scene_threshold,
        )
        return {
                "video_manifest_id": str(record.id),
                "visual_status": record.status.value,
                "scene_count": len(record.manifest.scenes) if record.manifest else 0,
        }
    return execute


def _serve() -> None:
    # Before the models load: only `channel` may write to the real stdout.
    channel = open_protocol_channel()
    execute = _runner()
    logger.info("visual_executor_ready")
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            if request.get("control") == "ping":
                channel.send({"id": request.get("id"), "result": {"ok": True}})
                continue
            payload = dict(request.get("payload") or {})
            logger.info(
                "visual_job_received",
                extra={
                    "workspace_id": payload.get("workspace_id"),
                    "project_id": payload.get("project_id"),
                    "video_id": payload.get("video_id"),
                },
            )
            response = {
                "id": request.get("id"),
                "result": execute(payload),
            }
        except Exception as exc:
            response = error_reply(request.get("id"), exc)
        channel.send(response)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--video-id", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
