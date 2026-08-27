from __future__ import annotations

from dataclasses import asdict

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.orchestration.contracts import (
    EnsureProposalRequest,
    EnsureProposalResult,
    assert_identifier_only_history,
)
from app.schemas.workflow_proposals import WorkflowProposalEnsureResponse


class WorkflowProposalActivities:
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

    @activity.defn(name="ensure_workflow_proposal")
    async def ensure_workflow_proposal(
        self,
        request: EnsureProposalRequest,
    ) -> EnsureProposalResult:
        assert_identifier_only_history(request)
        if not self.api_base_url or not self._worker_token:
            raise ApplicationError(
                "Internal proposal API transport is not configured.",
                type="workflow_proposal_transport_missing",
                non_retryable=True,
            )
        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(
                f"{self.api_base_url}/internal/workflows/proposals/ensure",
                headers={"x-motionly-worker-token": self._worker_token},
                json=asdict(request),
            )
            if response.status_code >= 400:
                raise ApplicationError(
                    "Canonical workflow proposal could not be ensured.",
                    type=(
                        "workflow_proposal_rejected"
                        if 400 <= response.status_code < 500
                        else "workflow_proposal_unavailable"
                    ),
                    non_retryable=400 <= response.status_code < 500,
                )
            try:
                payload = WorkflowProposalEnsureResponse.model_validate(response.json())
            except Exception:
                raise ApplicationError(
                    "Internal proposal API returned an invalid identifier schema.",
                    type="workflow_proposal_response_invalid",
                    non_retryable=True,
                ) from None
            result = EnsureProposalResult(
                workflow_run_id=str(payload.workflow_run_id),
                proposal_id=str(payload.proposal_id),
                proposal_revision=payload.proposal_revision,
                request_id=payload.request_id,
                context_digest=payload.context_digest,
                proposal_state=payload.proposal_state,
            )
            if result.workflow_run_id != request.workflow_run_id:
                raise ApplicationError(
                    "Internal proposal API returned a conflicting workflow identity.",
                    type="workflow_proposal_identity_conflict",
                    non_retryable=True,
                )
            assert_identifier_only_history(result)
            return result
        except ApplicationError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise ApplicationError(
                "Internal proposal API is temporarily unavailable.",
                type="workflow_proposal_unavailable",
            ) from None
        finally:
            if owned_client:
                await client.aclose()
