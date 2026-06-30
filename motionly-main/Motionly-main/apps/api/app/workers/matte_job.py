import argparse
import json
import sys
from uuid import UUID

from app.api.errors import ApiError
from app.core.config import get_settings
from app.repositories.uploads import SupabaseUploadRepository
from app.repositories.visual import SupabaseVisualRepository
from app.services.matte_jobs import run_matte_for_upload
from app.services.visual_intelligence import LocalMLVisualIntelligenceProvider


def main() -> None:
    if "--serve" in sys.argv:
        _serve()
        return
    args = _arguments()
    result = _runner()(vars(args))
    print(json.dumps(result))


def _runner():
    settings = get_settings()
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
    provider = LocalMLVisualIntelligenceProvider(
        clip_model=settings.visual_clip_model,
        blip_model=settings.visual_blip_model,
        yolo_model=settings.visual_yolo_model,
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
            provider_models=provider._load_models(),
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
    execute = _runner()
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            if request.get("control") == "ping":
                print(json.dumps({"id": request.get("id"), "result": {"ok": True}}), flush=True)
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
        print(json.dumps(response), flush=True)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--matte-id")
    return parser.parse_args()


if __name__ == "__main__":
    main()
