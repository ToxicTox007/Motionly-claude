import argparse
import json
import sys
from uuid import UUID

from app.api.errors import ApiError
from app.core.config import get_settings
from app.core.production import validate_production_settings
from app.repositories.uploads import SupabaseUploadRepository
from app.repositories.visual import SupabaseVisualRepository
from app.services.cpu_vision import SELFIE_SEGMENTER_LANDSCAPE, load_cpu_matte_models
from app.services.matte_jobs import run_matte_for_upload
from app.services.model_domain_adapters import register_matte_route
from app.services.model_gateway import ModelOperation, validate_gateway_operations
from app.services.model_gateway_factory import model_gateway_from_settings
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.visual_intelligence import LocalMLVisualIntelligenceProvider
from app.workers.executor_protocol import open_protocol_channel


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
    matte_provider_key = str(getattr(settings, "matte_provider", "local_ml")).strip().lower()
    matte_revision = "mediapipe-selfie-segmentation-v1"
    matte_route_identity: dict[str, str] = {}
    if visual_provider_key == "fixture":
        provider_models = {}
    elif matte_provider_key == "local_cpu":
        # MediaPipe Tasks on CPU: no torch/transformers model is loaded.
        provider_models = load_cpu_matte_models(
            model_path=settings.matte_cpu_model_path,
            model_sha256=settings.matte_cpu_model_sha256,
        )
        matte_revision = f"sha256:{settings.matte_cpu_model_sha256.strip().lower()}"
        matte_route_identity = {
            "provider": "local_mediapipe_cpu",
            "model": SELFIE_SEGMENTER_LANDSCAPE.name,
        }
    else:
        provider = LocalMLVisualIntelligenceProvider(
            clip_model=settings.visual_clip_model,
            blip_model=settings.visual_blip_model,
            yolo_model=settings.visual_yolo_model,
        )
        provider_models = provider._load_models()

    operations = OperationsRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    gateway = model_gateway_from_settings(
        settings,
        metrics_registry=MetricsRegistry(),
        operations_repository=operations,
    )
    matte_route = None
    if gateway is not None and provider_models:
        matte_route = register_matte_route(
            gateway,
            provider_models=provider_models,
            revision=matte_revision,
            **matte_route_identity,
        )
    if settings.app_env == "production":
        validate_gateway_operations(
            gateway,
            required=(ModelOperation.VISION_MATTE_SEGMENTATION,),
            runtime="matte worker",
        )

    def execute(payload: dict[str, object]) -> dict[str, object]:
        record = run_matte_for_upload(
            repository=repository,
            workspace_id=UUID(str(payload["workspace_id"])),
            project_id=UUID(str(payload["project_id"])),
            video_id=UUID(str(payload["video_id"])),
            matte_id=UUID(str(payload["matte_id"])) if payload.get("matte_id") else None,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            provider_models=provider_models,
            model_gateway=gateway,
            matte_gateway_route=matte_route,
            production=settings.app_env == "production",
        )
        return {
            "matte_id": str(record.id),
            "matte_status": record.status.value,
            "cache_key": record.cache_key,
            "failed": record.status.value == "failed",
            "error_code": record.error_code,
            "error_message": record.error_message,
        }

    return execute


def _serve() -> None:
    # Before the models load: only `channel` may write to the real stdout.
    channel = open_protocol_channel()
    execute = _runner()
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            if request.get("control") == "ping":
                channel.send({"id": request.get("id"), "result": {"ok": True}})
                continue
            response = {
                "id": request.get("id"),
                "result": execute(dict(request.get("payload") or {})),
            }
        except ApiError as exc:
            response = {"id": request.get("id"), "error": exc.message, "error_code": exc.code}
        except Exception as exc:
            from app.core.logging import redact_string

            response = {
                "id": request.get("id"),
                "error": redact_string(str(exc)),
                "error_code": type(exc).__name__,
            }
        channel.send(response)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--matte-id")
    return parser.parse_args()


if __name__ == "__main__":
    main()
