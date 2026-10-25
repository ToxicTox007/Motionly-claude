from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.repositories.captions import TranscriptRecord
from app.schemas.captions import LanguageHint, TranscriptWord
from app.schemas.plans import PlanCreateRequest, PlannerProviderResult, StyleClassification
from app.schemas.visual import VideoManifest
from app.services.edit_planner import HttpEditPlannerProvider, _planner_request_body
from app.services.model_gateway import (
    ChatMessage,
    ModelCallContext,
    ModelGateway,
    PlanningOutput,
    PlanningRequest,
    TranscriptionOutput,
    TranscriptionRequest,
)
from app.services.transcription_provider import (
    TranscriptionResult,
    normalize_transcription_language,
)


@dataclass(frozen=True, kw_only=True)
class GatewayEditPlannerProvider(HttpEditPlannerProvider):
    """Gateway-only planner bridge: keeps plan validation while routing model I/O.

    Planning runs only through the provider-neutral model gateway (Task 10). The
    gateway's :class:`PlanFeatureQuotaGuard` records and (under
    ``AI_QUOTA_SCOPE=feature``) enforces the ``planning`` feature quota on each
    call, so the bridge no longer needs a billing repository of its own.
    """

    gateway: ModelGateway = field(repr=False, compare=False)

    def create_plan(
        self,
        *,
        transcript: TranscriptRecord,
        payload: PlanCreateRequest,
        video_duration_ms: int,
        visual_manifest: VideoManifest | None = None,
        style_classification: StyleClassification | None = None,
        billing_repository: Any = None,
        operations_repository: Any = None,
    ) -> PlannerProviderResult:
        # Planning quota is metered inside the gateway (the route carries the
        # ``planning`` budget_feature), not by the planner bridge. The repository
        # arguments are accepted for the EditPlannerProvider protocol only.
        result = super().create_plan(
            transcript=transcript,
            payload=payload,
            video_duration_ms=video_duration_ms,
            visual_manifest=visual_manifest,
            style_classification=style_classification,
            billing_repository=billing_repository,
            operations_repository=operations_repository,
        )
        usage = result.raw_payload.get("usage")
        metadata = usage.get("_motionly_gateway") if isinstance(usage, dict) else None
        if not isinstance(metadata, dict):
            return result
        model_choice = usage.get("_motionly_model_choice") if isinstance(usage, dict) else None
        clean_usage = {
            key: value
            for key, value in usage.items()
            if key not in {"_motionly_gateway", "_motionly_model_choice"}
        }
        raw_payload = {
            **result.raw_payload,
            "usage": clean_usage,
            "model_gateway": metadata,
        }
        if isinstance(model_choice, dict):
            # Plan response provenance (plan Section 2.11): the user's chosen vs.
            # served model and any fallback reason.
            raw_payload["model_choice"] = model_choice
        return result.model_copy(
            update={
                "provider": str(metadata.get("provider") or result.provider),
                "model": str(metadata.get("model") or result.model),
                "cost_estimate_usd": float(
                    metadata.get("actual_cost_usd") or result.cost_estimate_usd or 0.0
                ),
                "raw_payload": raw_payload,
            }
        )

    def _request_plan(
        self,
        *,
        transcript: TranscriptRecord,
        payload: PlanCreateRequest,
        style,
        video_duration_ms: int,
        visual_manifest: VideoManifest | None,
        style_classification: StyleClassification | None,
        retry_instruction: str | None,
        validation_errors: list[str],
        minimal: bool,
    ) -> dict[str, object]:
        body = _planner_request_body(
            model=self.model,
            transcript=transcript,
            payload=payload,
            style=style,
            video_duration_ms=video_duration_ms,
            visual_manifest=visual_manifest,
            style_classification=style_classification,
            retry_instruction=retry_instruction,
            validation_errors=validation_errors,
            minimal=minimal,
        )
        raw_messages = body.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError("planner messages are invalid")
        messages = tuple(
            ChatMessage(role=str(item["role"]), content=str(item["content"]))
            for item in raw_messages
            if isinstance(item, dict) and item.get("role") and item.get("content")
        )
        response_format = body.get("response_format")
        result = self.gateway.invoke(
            PlanningRequest(
                messages=messages,
                response_format=(
                    response_format if isinstance(response_format, dict) else None
                ),
                temperature=float(body.get("temperature") or 0.0),
            ),
            context=ModelCallContext(
                workspace_id=transcript.workspace_id,
                project_id=transcript.project_id,
            ),
        )
        if not isinstance(result.output, PlanningOutput):
            raise TypeError("planning route returned the wrong typed output")
        raw_response = dict(result.output.raw_response)
        usage = raw_response.get("usage")
        typed_usage = (
            dict(usage)
            if isinstance(usage, dict)
            else {
                "prompt_tokens": result.metadata.usage.input_units,
                "completion_tokens": result.metadata.usage.output_units,
                "total_tokens": result.metadata.usage.total_units,
            }
        )
        typed_usage["_motionly_gateway"] = result.metadata.as_dict()
        # Plan Section 2.11 / Task 31: carry the model choice provenance into the
        # plan response so the UI can tell the user which model served, and when
        # a fallback happened.
        if result.model_choice is not None:
            typed_usage["_motionly_model_choice"] = result.model_choice.as_dict()
        raw_response["usage"] = typed_usage
        return raw_response


class GatewayTranscriptionProvider:
    """Legacy transcription contract backed by the provider-neutral model gateway."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        provider_name: str,
        model: str,
        model_revision: str,
    ) -> None:
        self.gateway = gateway
        self.provider_name = provider_name
        self.model = model
        self.model_revision = model_revision

    def transcribe(
        self,
        *,
        audio_path: Path | None,
        language_hint: LanguageHint,
    ) -> TranscriptionResult:
        return self.transcribe_with_context(
            audio_path=audio_path,
            language_hint=language_hint,
            workspace_id=None,
            project_id=None,
        )

    def transcribe_with_context(
        self,
        *,
        audio_path: Path | None,
        language_hint: LanguageHint,
        workspace_id: object | None,
        project_id: object | None,
    ) -> TranscriptionResult:
        if audio_path is None:
            raise ValueError("audio_path is required for gateway transcription")
        result = self.gateway.invoke(
            TranscriptionRequest(
                audio_path=audio_path,
                language=(language_hint.value if language_hint != LanguageHint.AUTO else None),
            ),
            context=ModelCallContext(
                workspace_id=workspace_id,
                project_id=project_id,
            ),
        )
        if not isinstance(result.output, TranscriptionOutput):
            raise TypeError("transcription route returned the wrong typed output")
        raw_payload = dict(result.output.raw_response)
        raw_payload["model_gateway"] = result.metadata.as_dict()
        return TranscriptionResult(
            language=normalize_transcription_language(result.output.language, language_hint),
            confidence=result.output.confidence,
            provider=result.metadata.provider,
            model=result.metadata.model,
            words=[
                TranscriptWord(
                    index=index,
                    text=word.text,
                    start_ms=word.start_ms,
                    end_ms=word.end_ms,
                    confidence=word.confidence,
                )
                for index, word in enumerate(result.output.words)
            ],
            raw_payload=raw_payload,
        )
