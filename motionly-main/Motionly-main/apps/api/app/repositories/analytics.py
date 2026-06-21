from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.analytics import (
    AIEditAnalyticsSummary,
    AIEditFeedbackEventResponse,
    AIEditFeedbackEventType,
    AIEditLayer,
    AIEditLayerSummary,
    FineTuningExample,
    FineTuningSignal,
)


@dataclass
class AIEditFeedbackEventRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    event_type: AIEditFeedbackEventType
    layer: AIEditLayer
    source: str
    edit_plan_id: UUID | None = None
    decision_id: str | None = None
    action: str | None = None
    ai_payload: dict[str, Any] = field(default_factory=dict)
    user_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> AIEditFeedbackEventResponse:
        return AIEditFeedbackEventResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            edit_plan_id=self.edit_plan_id,
            user_id=self.user_id,
            event_type=self.event_type,
            layer=self.layer,
            decision_id=self.decision_id,
            action=self.action,
            source=self.source,
            ai_payload=self.ai_payload,
            user_payload=self.user_payload,
            metadata=self.metadata,
            created_at=self.created_at,
        )


class InMemoryAnalyticsRepository:
    def __init__(self) -> None:
        self.events: dict[UUID, AIEditFeedbackEventRecord] = {}

    def record_event(
        self,
        *,
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
    ) -> AIEditFeedbackEventRecord:
        record = AIEditFeedbackEventRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            edit_plan_id=edit_plan_id,
            event_type=event_type,
            layer=layer,
            decision_id=decision_id,
            action=action,
            source=source,
            ai_payload=ai_payload or {},
            user_payload=user_payload or {},
            metadata=metadata or {},
        )
        self.events[record.id] = record
        return record

    def list_events(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        layer: AIEditLayer | None = None,
        limit: int = 100,
    ) -> list[AIEditFeedbackEventRecord]:
        events = [
            event
            for event in self.events.values()
            if event.project_id == project_id
            and event.workspace_id == workspace_id
            and (layer is None or event.layer == layer)
        ]
        return sorted(events, key=lambda event: event.created_at, reverse=True)[:limit]

    def summarize(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> AIEditAnalyticsSummary:
        events = self.list_events(
            project_id=project_id,
            workspace_id=workspace_id,
            limit=10_000,
        )
        layers = []
        for layer in AIEditLayer:
            layer_events = [event for event in events if event.layer == layer]
            suggested = _count(layer_events, AIEditFeedbackEventType.AI_SUGGESTED)
            kept = _count(layer_events, AIEditFeedbackEventType.KEPT)
            changed = _count(layer_events, AIEditFeedbackEventType.CHANGED)
            rejected = _count(layer_events, AIEditFeedbackEventType.REJECTED)
            denominator = kept + changed + rejected
            layers.append(
                AIEditLayerSummary(
                    layer=layer,
                    suggested=suggested,
                    kept=kept,
                    changed=changed,
                    rejected=rejected,
                    keep_rate=round(kept / denominator, 4) if denominator else 0,
                )
            )
        return AIEditAnalyticsSummary(
            total_events=len(events),
            training_example_count=len(self.fine_tuning_examples(events=events)),
            layers=layers,
        )

    def fine_tuning_examples(
        self,
        *,
        project_id: UUID | None = None,
        workspace_id: UUID | None = None,
        events: list[AIEditFeedbackEventRecord] | None = None,
        limit: int = 250,
    ) -> list[FineTuningExample]:
        source_events = events
        if source_events is None:
            source_events = [
                event
                for event in self.events.values()
                if (project_id is None or event.project_id == project_id)
                and (workspace_id is None or event.workspace_id == workspace_id)
            ]
        examples = [
            example
            for event in sorted(source_events, key=lambda item: item.created_at, reverse=True)
            if (example := _fine_tuning_example_for_event(event)) is not None
        ]
        return examples[:limit]


class SupabaseAnalyticsRepository(InMemoryAnalyticsRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__()
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def record_event(
        self,
        *,
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
    ) -> AIEditFeedbackEventRecord:
        payload = {
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "edit_plan_id": str(edit_plan_id) if edit_plan_id else None,
            "user_id": str(user_id),
            "event_type": event_type.value,
            "layer": layer.value,
            "decision_id": decision_id,
            "action": action,
            "source": source,
            "ai_payload": ai_payload or {},
            "user_payload": user_payload or {},
            "metadata": metadata or {},
        }
        response = httpx.post(
            f"{self.rest_url}/ai_edit_feedback_events",
            headers=self.headers,
            json=[payload],
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._event_from_row(response.json()[0])

    def list_events(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        layer: AIEditLayer | None = None,
        limit: int = 100,
    ) -> list[AIEditFeedbackEventRecord]:
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "project_id": f"eq.{project_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if layer:
            params["layer"] = f"eq.{layer.value}"
        response = httpx.get(
            f"{self.rest_url}/ai_edit_feedback_events",
            headers=self.headers,
            params=params,
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._event_from_row(row) for row in response.json()]

    def _event_from_row(self, row: dict[str, Any]) -> AIEditFeedbackEventRecord:
        return AIEditFeedbackEventRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            edit_plan_id=UUID(row["edit_plan_id"]) if row.get("edit_plan_id") else None,
            user_id=UUID(row["user_id"]),
            event_type=AIEditFeedbackEventType(row["event_type"]),
            layer=AIEditLayer(row["layer"]),
            decision_id=row.get("decision_id"),
            action=row.get("action"),
            source=row["source"],
            ai_payload=row.get("ai_payload") or {},
            user_payload=row.get("user_payload") or {},
            metadata=row.get("metadata") or {},
            created_at=_parse_datetime(row["created_at"]),
        )

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if not response.is_success:
            raise ApiError(502, "supabase_request_failed", "Supabase request failed.")


def _count(
    events: list[AIEditFeedbackEventRecord],
    event_type: AIEditFeedbackEventType,
) -> int:
    return len([event for event in events if event.event_type == event_type])


def _fine_tuning_example_for_event(
    event: AIEditFeedbackEventRecord,
) -> FineTuningExample | None:
    if event.event_type == AIEditFeedbackEventType.KEPT:
        ideal = event.user_payload or event.ai_payload
        if not ideal:
            return None
        return FineTuningExample(
            event_id=event.id,
            project_id=event.project_id,
            edit_plan_id=event.edit_plan_id,
            layer=event.layer,
            signal=FineTuningSignal.POSITIVE,
            weight=0.55,
            decision_id=event.decision_id,
            input={"layer": event.layer.value, "source": event.source, **event.metadata},
            ideal_output=ideal,
            rejected_output=None,
            created_at=event.created_at,
        )
    if event.event_type == AIEditFeedbackEventType.CHANGED and event.user_payload:
        return FineTuningExample(
            event_id=event.id,
            project_id=event.project_id,
            edit_plan_id=event.edit_plan_id,
            layer=event.layer,
            signal=FineTuningSignal.CORRECTION,
            weight=0.9,
            decision_id=event.decision_id,
            input={"layer": event.layer.value, "source": event.source, **event.metadata},
            ideal_output=event.user_payload,
            rejected_output=event.ai_payload or None,
            created_at=event.created_at,
        )
    if event.event_type == AIEditFeedbackEventType.REJECTED and event.ai_payload:
        return FineTuningExample(
            event_id=event.id,
            project_id=event.project_id,
            edit_plan_id=event.edit_plan_id,
            layer=event.layer,
            signal=FineTuningSignal.REJECTION,
            weight=0.75,
            decision_id=event.decision_id,
            input={"layer": event.layer.value, "source": event.source, **event.metadata},
            ideal_output={},
            rejected_output=event.ai_payload,
            created_at=event.created_at,
        )
    return None


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
