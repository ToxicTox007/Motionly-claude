"""Optimistic concurrency control exceptions for plan revision enforcement."""

from fastapi import Request
from fastapi.responses import JSONResponse


class RevisionConflictError(Exception):
    """Raised when an update targets a stale plan revision.

    The frontend should re-fetch the current plan and retry or prompt the user.
    """

    def __init__(self, plan_id: str, expected: int, actual: int) -> None:
        self.plan_id = plan_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Revision conflict: expected {expected}, actual {actual}")


async def revision_conflict_error_handler(
    request: Request,
    exc: RevisionConflictError,
) -> JSONResponse:
    """Return a machine-parseable 409 response for revision conflicts."""
    return JSONResponse(
        status_code=409,
        content={
            "error": "revision_conflict",
            "expected_revision": exc.expected,
            "current_revision": exc.actual,
            "plan_id": exc.plan_id,
            "message": str(exc),
        },
    )
