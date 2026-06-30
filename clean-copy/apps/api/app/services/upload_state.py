from app.api.errors import ApiError
from app.schemas.uploads import UploadStatus

ALLOWED_TRANSITIONS: dict[UploadStatus, set[UploadStatus]] = {
    UploadStatus.CREATED: {UploadStatus.UPLOADING, UploadStatus.FAILED},
    UploadStatus.UPLOADING: {UploadStatus.UPLOADED, UploadStatus.FAILED},
    UploadStatus.UPLOADED: {UploadStatus.PROCESSING, UploadStatus.FAILED},
    UploadStatus.PROCESSING: {UploadStatus.READY, UploadStatus.FAILED},
    UploadStatus.READY: set(),
    UploadStatus.FAILED: {UploadStatus.UPLOADING, UploadStatus.PROCESSING},
}


def assert_upload_transition(current: UploadStatus, next_status: UploadStatus) -> None:
    if next_status not in ALLOWED_TRANSITIONS[current]:
        raise ApiError(
            409,
            "upload_invalid_state_transition",
            f"Upload cannot transition from {current} to {next_status}.",
        )
