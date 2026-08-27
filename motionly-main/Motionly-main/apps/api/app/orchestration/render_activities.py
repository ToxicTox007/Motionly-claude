from __future__ import annotations

from dataclasses import asdict

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.orchestration.contracts import (
    EnsureDraftRenderRequest,
    EnsureDraftRenderResult,
    assert_identifier_only_history,
)
from app.schemas.workflow_renders import WorkflowDraftRenderEnsureResponse


class WorkflowRenderActivities:
    def __init__(
        self,
        *,
        api_base_url: str,
        worker_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self._worker_token = worker_token
        self._client = client

    @activity.defn(name="ensure_workflow_draft_render")
    async def ensure_workflow_draft_render(
        self,
        request: EnsureDraftRenderRequest,
    ) -> EnsureDraftRenderResult:
        assert_identifier_only_history(request)
        if not self.api_base_url or not self._worker_token:
            raise ApplicationError(
                "Internal draft-render API transport is not configured.",
                type="workflow_render_transport_missing",
                non_retryable=True,
            )
        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(
                f"{self.api_base_url}/internal/workflows/draft-renders/ensure",
                headers={"x-motionly-worker-token": self._worker_token},
                json=asdict(request),
            )
            if response.status_code >= 400:
                raise ApplicationError(
                    "Canonical workflow draft render could not be ensured.",
                    type=(
                        "workflow_render_rejected"
                        if 400 <= response.status_code < 500
                        else "workflow_render_unavailable"
                    ),
                    non_retryable=400 <= response.status_code < 500,
                )
            try:
                payload = WorkflowDraftRenderEnsureResponse.model_validate(response.json())
            except Exception:
                raise ApplicationError(
                    "Internal draft-render API returned an invalid identifier schema.",
                    type="workflow_render_response_invalid",
                    non_retryable=True,
                ) from None
            result = EnsureDraftRenderResult(
                workflow_run_id=str(payload.workflow_run_id),
                render_job_id=str(payload.render_job_id),
                props_hash=payload.props_hash,
                render_engine=payload.render_engine.value,
                render_queue=payload.render_queue,
                source_plan_revision=payload.source_plan_revision,
                source_media_revision=payload.source_media_revision,
            )
            if result.workflow_run_id != request.workflow_run_id:
                raise ApplicationError(
                    "Internal draft-render API returned a conflicting workflow identity.",
                    type="workflow_render_identity_conflict",
                    non_retryable=True,
                )
            assert_identifier_only_history(result)
            return result
        except ApplicationError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise ApplicationError(
                "Internal draft-render API is temporarily unavailable.",
                type="workflow_render_unavailable",
            ) from None
        finally:
            if owned_client:
                await client.aclose()
