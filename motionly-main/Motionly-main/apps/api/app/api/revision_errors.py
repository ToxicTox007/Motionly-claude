"""Optimistic concurrency control exceptions for plan revision enforcement."""

from fastapi import Request
from fastapi.responses import JSONResponse

# Single canonical error code for every plan revision conflict. All conflict
# producers (plan routes and layer routes that recompile the plan) funnel
# through RevisionConflictError so the frontend only ever has to recognise one
# code/shape.
REVISION_CONFLICT_CODE = "collaboration_revision_conflict"


class RevisionConflictError(Exception):
    """Raised when an update targets a stale plan revision.

    The frontend should re-fetch the current plan and retry or prompt the user.
    """

    def __init__(self, plan_id: str, expected: int, actual: int) -> None:
        self.plan_id = plan_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Revision conflict: expected {expected}, actual {actual}")


def _request_id(request: Request) -> str | None:
    state = getattr(request, "state", None)
    request_id = getattr(state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


async def revision_conflict_error_handler(
    request: Request,
    exc: RevisionConflictError,
) -> JSONResponse:
    """Return a machine-parseable 409 response for revision conflicts.

    Uses the standard ``{"error": {code, message, request_id}}`` envelope shared
    with :func:`app.api.errors.api_error_handler`, plus the top-level
    ``expected_revision`` / ``current_revision`` / ``plan_id`` fields the editor
    reads to drive the conflict dialog.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": REVISION_CONFLICT_CODE,
                "message": str(exc),
                "request_id": _request_id(request),
            },
            "expected_revision": exc.expected,
            "current_revision": exc.actual,
            "plan_id": str(exc.plan_id),
        },
    )
