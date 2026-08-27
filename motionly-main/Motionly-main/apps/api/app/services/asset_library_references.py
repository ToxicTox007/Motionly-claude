"""Asset-library media reference policy.

Asset-library rows are an index over project-owned media. They never own a raw
storage location: the durable ObjectReference remains the sole authority for
location, tenant, and lifecycle state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.api.errors import ApiError
from app.schemas.localized_publishing import AssetLibraryKind, AssetLibraryMediaState
from app.services.object_references import ObjectReference, ObjectReferenceService

_MEDIA_REFERENCE_KINDS: dict[AssetLibraryKind, tuple[str, str]] = {
    AssetLibraryKind.UPLOAD: ("uploaded_video", "source_video"),
    AssetLibraryKind.EXPORT: ("export", "export"),
    AssetLibraryKind.BROLL: ("stock_asset", "broll_asset"),
}


@dataclass(frozen=True)
class AssetLibraryReferenceBinding:
    workspace_id: UUID
    source_project_id: UUID | None
    source_asset_id: UUID | None
    object_reference_id: UUID | None
    kind: AssetLibraryKind
    media_state: AssetLibraryMediaState


def allowed_asset_library_buckets(settings: object | None) -> frozenset[str]:
    """Return only configured first-party media buckets, never a client value."""
    values = {
        str(getattr(settings, "upload_storage_bucket", "raw-uploads")).strip(),
        str(getattr(settings, "export_storage_bucket", "exports")).strip(),
    }
    return frozenset(value for value in values if value)


def is_asset_library_media(kind: AssetLibraryKind) -> bool:
    return kind in _MEDIA_REFERENCE_KINDS


async def resolve_asset_library_reference(
    *,
    binding: AssetLibraryReferenceBinding,
    object_reference_service: ObjectReferenceService,
    allowed_buckets: Iterable[str],
) -> ObjectReference:
    """Prove an indexed media item still resolves to its active owned object.

    This intentionally repeats validation for every caller rather than trusting
    an earlier indexing result. Object-reference rows may be soft-deleted or
    altered by retention between indexing and use.
    """
    if not is_asset_library_media(binding.kind):
        raise ApiError(
            409,
            "asset_library_media_not_applicable",
            "This library item does not represent reusable managed media.",
        )
    if (
        binding.media_state != AssetLibraryMediaState.VERIFIED
        or binding.object_reference_id is None
        or binding.source_project_id is None
        or binding.source_asset_id is None
    ):
        raise ApiError(
            409,
            "asset_library_media_unavailable",
            "This media item is unavailable pending reference recovery.",
        )

    try:
        reference = await object_reference_service.get_active_reference(
            workspace_id=binding.workspace_id,
            project_id=binding.source_project_id,
            reference_id=binding.object_reference_id,
        )
    except ApiError as exc:
        raise ApiError(
            409,
            "asset_library_media_unavailable",
            "This media item no longer has an active owned reference.",
        ) from exc

    expected_owner_type, expected_object_type = _MEDIA_REFERENCE_KINDS[binding.kind]
    expected_prefix = (
        f"workspaces/{binding.workspace_id}/projects/{binding.source_project_id}/"
    )
    if (
        reference.workspace_id != binding.workspace_id
        or reference.project_id != binding.source_project_id
        or reference.owner_id != binding.source_asset_id
        or reference.owner_type != expected_owner_type
        or reference.object_type != expected_object_type
        or reference.storage_bucket not in set(allowed_buckets)
        or not reference.storage_path.startswith(expected_prefix)
    ):
        raise ApiError(
            409,
            "asset_library_media_invalid",
            "This media reference does not match the indexed tenant-owned asset.",
        )
    return reference
