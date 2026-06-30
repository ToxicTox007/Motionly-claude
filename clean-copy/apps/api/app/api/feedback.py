import httpx
from fastapi import APIRouter, Request

from app.api.errors import ApiError
from app.schemas.feedback import LaunchFeedbackCreateRequest, LaunchFeedbackCreateResponse

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=LaunchFeedbackCreateResponse, status_code=201)
async def create_launch_feedback(
    payload: LaunchFeedbackCreateRequest, request: Request
) -> LaunchFeedbackCreateResponse:
    settings = request.app.state.settings
    record = {
        **payload.model_dump(mode="json"),
        "user_agent": request.headers.get("user-agent", "")[:500] or None,
    }
    if settings.supabase_url and settings.supabase_service_role_key:
        response = httpx.post(
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
