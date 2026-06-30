import argparse
import json
import sys
from uuid import UUID

from app.api.errors import ApiError
from app.core.config import get_settings
from app.repositories.uploads import SupabaseUploadRepository
from app.repositories.visual import SupabaseVisualRepository
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
    from app.services.visual_intelligence import run_visual_analysis_for_upload

    def execute(payload: dict[str, object]) -> dict[str, object]:
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
            response = {
                "id": request.get("id"),
                "error": exc.message,
                "error_code": exc.code,
            }
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
    return parser.parse_args()


if __name__ == "__main__":
    main()
