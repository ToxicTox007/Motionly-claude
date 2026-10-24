import httpx
from fastapi import APIRouter, Request

from app.api.auth import enforce_hourly_limit
from app.api.errors import ApiError
from app.schemas.feedback import LaunchFeedbackCreateRequest, LaunchFeedbackCreateResponse
from app.services.offload import run_blocking

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=LaunchFeedbackCreateResponse, status_code=201)
async def create_launch_feedback(
    payload: LaunchFeedbackCreateRequest, request: Request
) -> LaunchFeedbackCreateResponse:
    settings = request.app.state.settings
    # Unauthenticated and written with the service-role key: bound how much
    # one network (and everyone together) can submit.
    await enforce_hourly_limit(
        request,
        action="feedback",
        per_viewer=getattr(settings, "feedback_rate_limit_per_hour", 10),
        overall=getattr(settings, "feedback_global_rate_limit_per_hour", 500),
    )
    record = {
        **payload.model_dump(mode="json"),
        "user_agent": request.headers.get("user-agent", "")[:500] or None,
    }
    if settings.supabase_url and settings.supabase_service_role_key:
        # Synchronous HTTP (10s timeout): keep it off the event loop.
        response = await run_blocking(
            httpx.post,
            f"{str(settings.supabase_url).rstrip('/')}/rest/v1/launch_feedback",
            headers={
                "apikey": settings.supabase_service_role_key,
                "authorization": f"Bearer {settings.supabase_service_role_key}",
                "content-type": "application/json",
                "prefer": "return=minimal",
            },
            json=record,
            timeout=10,
        )
        if response.status_code >= 400:
            raise ApiError(502, "feedback_save_failed", "Feedback could not be saved.")
    else:
        request.app.state.launch_feedback.append(record)
    return LaunchFeedbackCreateResponse()
