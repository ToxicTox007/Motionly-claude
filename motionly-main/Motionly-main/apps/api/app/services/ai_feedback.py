from typing import Any
from uuid import UUID

from fastapi import Request

from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.plans import EditPlanRecord
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer


def record_ai_feedback(
    *,
    request: Request | None,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    event_type: AIEditFeedbackEventType,
    layer: AIEditLayer,
    source: str,
    edit_plan_id: UUID | None = None,
    decision_id: str | None = None,
    action: str | None = None,
    ai_payload: dict[str, Any] | None = None,
    user_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    repository = _analytics_repository(request)
    if repository is None:
        return
    try:
        repository.record_event(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            edit_plan_id=edit_plan_id,
            event_type=event_type,
            layer=layer,
            decision_id=decision_id,
            action=action,
            source=source,
            ai_payload=ai_payload,
            user_payload=user_payload,
            metadata=metadata,
        )
    except Exception:
        # Feedback is diagnostic and training data; it should never block editing.
        return


def record_plan_suggestions(
    *,
    request: Request | None,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    plan: EditPlanRecord,
) -> None:
    for layer, payload in (
        (AIEditLayer.CUTS, {"cuts": [cut.model_dump(mode="json") for cut in plan.plan.cuts]}),
        (
            AIEditLayer.BROLL,
            {
                "broll_density": plan.broll_density.value,
                "broll_requests": [
                    request.model_dump(mode="json") for request in plan.plan.broll_requests
                ],
            },
        ),
        (AIEditLayer.MUSIC, {"music_mood": plan.plan.music_mood}),
        (
            AIEditLayer.EFFECTS,
            {
                "color_grade": plan.plan.color_grade,
                "effects": plan.plan.effects,
                "zoom_moments": [
                    moment.model_dump(mode="json") for moment in plan.plan.zoom_moments
                ],
            },
        ),
        (
            AIEditLayer.CAPTIONS,
            {
                "caption_highlights": [
                    highlight.model_dump(mode="json")
                    for highlight in plan.plan.caption_highlights
                ],
            },
        ),
    ):
        record_ai_feedback(
            request=request,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            edit_plan_id=plan.id,
            event_type=AIEditFeedbackEventType.AI_SUGGESTED,
            layer=layer,
            source="edit_planner",
            decision_id=f"{plan.id}:{layer.value}",
            ai_payload=payload,
            metadata={
                "plan_version": plan.version,
                "style": plan.style.value,
                "provider": plan.provider,
                "model": plan.model,
            },
        )


def record_render_kept_signals(
    *,
    request: Request | None,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    edit_plan_id: UUID | None,
    render_job_id: UUID,
    render_props: dict[str, Any],
) -> None:
    edit_plan = render_props.get("editPlan") if isinstance(render_props, dict) else {}
    layers: tuple[tuple[AIEditLayer, dict[str, Any]], ...] = (
        (AIEditLayer.CUTS, {"cuts": _dict(edit_plan).get("cuts", [])}),
        (AIEditLayer.CAPTIONS, {"captions": render_props.get("captions", [])}),
        (AIEditLayer.BROLL, {"assets": render_props.get("assets", [])}),
        (AIEditLayer.MUSIC, {"music_selection": render_props.get("musicSelection")}),
        (AIEditLayer.EFFECTS, {"effects_config": render_props.get("effectsConfig")}),
        (AIEditLayer.RENDER, {"export_settings": render_props.get("exportSettings")}),
    )
    for layer, payload in layers:
        if not _has_signal_payload(payload):
            continue
        record_ai_feedback(
            request=request,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            edit_plan_id=edit_plan_id,
            event_type=AIEditFeedbackEventType.KEPT,
            layer=layer,
            source="render_confirmation",
            decision_id=f"{render_job_id}:{layer.value}",
            user_payload=payload,
            metadata={"render_job_id": str(render_job_id)},
        )


def _analytics_repository(request: Request | None) -> InMemoryAnalyticsRepository | None:
    return getattr(getattr(request, "app", None), "state", None) and getattr(
        request.app.state,
        "analytics_repository",
        None,
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _has_signal_payload(payload: dict[str, Any]) -> bool:
    return any(value not in (None, [], {}) for value in payload.values())
