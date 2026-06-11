from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from app.api.errors import ApiError
from app.services.local_storage import LocalObjectStorage

router = APIRouter(prefix="/_local/storage", tags=["local storage"])


def _local_storage(request: Request) -> LocalObjectStorage:
    settings = request.app.state.settings
    if settings.app_env == "production":
        raise ApiError(404, "local_storage_unavailable", "Local storage is not available.")
    if settings.supabase_url and settings.supabase_service_role_key:
        raise ApiError(
            404,
            "local_storage_unavailable",
            "Local storage is disabled when Supabase Storage is configured.",
        )
    return LocalObjectStorage(root=settings.media_work_dir)


@router.put("/{bucket}/{object_path:path}", status_code=204)
async def put_local_object(
    bucket: str,
    object_path: str,
    request: Request,
) -> Response:
    content = await request.body()
    _local_storage(request).write_object(bucket=bucket, path=object_path, content=content)
    return Response(status_code=204)


@router.get("/{bucket}/{object_path:path}")
async def get_local_object(
    bucket: str,
    object_path: str,
    request: Request,
) -> FileResponse:
    path = _local_storage(request).ensure_object(bucket=bucket, path=object_path)
    return FileResponse(path)
