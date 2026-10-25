"""PostgREST-backed repository for persisted async inference operations.

Follows ``app/repositories/scale.py``: one ``SupabaseRestClient``, ``select``
projections, ``Prefer: return=representation`` (set by the client) and a numeric
``_raise`` on HTTP errors. Adds the two patterns Task 16A needs:

* idempotent ``reserve``: insert, and on a unique-violation (23505) fetch and
  return the existing row with ``created=False``;
* optimistic ``transition``: a conditional PATCH with ``state=in.(...)`` so the
  update only lands when the row is still in an expected state, and an empty
  representation means the optimistic check lost.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.services.inference_operations import (
    CAPABILITY_IDS,
    InferenceOperation,
    OperationNotFoundError,
    OperationState,
    TransitionConflictError,
    _reject_secret_values,
    _sanitize_error_code,
    sanitize_error_detail,
)
from app.services.supabase import SupabaseRestClient

INFERENCE_OPERATION_COLUMNS = (
    "id,workspace_id,project_id,capability,idempotency_key,provider_route_id,"
    "provider_model,provider_model_revision,route_snapshot,provider_operation_id,"
    "state,error_code,error_detail,estimated_cost_usd,actual_cost_usd,cost_unknown,"
    "created_at,updated_at,submitted_at,completed_at,resume_vars"
)

_PATH = "/inference_operations"
# PostgreSQL unique_violation.
_UNIQUE_VIOLATION = "23505"


class InferenceOperationRepositoryError(RuntimeError):
    """The inference-operation store could not satisfy a request."""


class SupabaseInferenceOperationRepository:
    """PostgREST implementation of ``InferenceOperationRepository``."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="inference_operations",
            metrics_registry=metrics_registry,
            timeout_seconds=15,
        )

    def reserve(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID | None,
        capability: str,
        idempotency_key: str,
        route_id: str,
        model: str,
        model_revision: str,
        route_snapshot: Mapping[str, Any],
        estimated_cost_usd: float | None = None,
    ) -> tuple[InferenceOperation, bool]:
        if capability not in CAPABILITY_IDS:
            raise ValueError(f"unknown capability {capability!r}")
        _reject_secret_values(route_snapshot)
        now = datetime.now(UTC)
        body = {
            "id": str(uuid4()),
            "workspace_id": str(workspace_id),
            "project_id": str(project_id) if project_id is not None else None,
            "capability": capability,
            "idempotency_key": idempotency_key,
            "provider_route_id": route_id,
            "provider_model": model,
            "provider_model_revision": model_revision,
            "route_snapshot": dict(route_snapshot),
            "state": OperationState.RESERVED.value,
            "estimated_cost_usd": estimated_cost_usd,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        response = self.client.request(
            "POST",
            _PATH,
            params={"select": INFERENCE_OPERATION_COLUMNS},
            json=body,
        )
        if response.status_code == 409 or self._is_unique_violation(response):
            existing = self.get_by_idempotency_key(workspace_id, idempotency_key)
            if existing is None:
                raise InferenceOperationRepositoryError(
                    "reserve hit a unique violation but no existing row was found"
                )
            return existing, False
        self._raise(response)
        return self._from_row(response.json()[0]), True

    def get(self, operation_id: UUID) -> InferenceOperation:
        response = self.client.request(
            "GET",
            _PATH,
            params={
                "id": f"eq.{operation_id}",
                "select": INFERENCE_OPERATION_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise OperationNotFoundError(str(operation_id))
        return self._from_row(rows[0])

    def get_by_idempotency_key(self, workspace_id: UUID, key: str) -> InferenceOperation | None:
        response = self.client.request(
            "GET",
            _PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "idempotency_key": f"eq.{key}",
                "select": INFERENCE_OPERATION_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        return self._from_row(rows[0]) if rows else None

    def transition(
        self,
        operation_id: UUID,
        *,
        to_state: OperationState,
        expected_from: set[OperationState] | None,
        provider_operation_id: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        actual_cost_usd: float | None = None,
        cost_unknown: bool | None = None,
    ) -> InferenceOperation:
        now = datetime.now(UTC)
        patch: dict[str, Any] = {"state": to_state.value, "updated_at": now.isoformat()}
        if provider_operation_id is not None:
            patch["provider_operation_id"] = provider_operation_id
        if error_code is not None:
            patch["error_code"] = _sanitize_error_code(error_code)
        if error_detail is not None:
            patch["error_detail"] = sanitize_error_detail(error_detail)
        if actual_cost_usd is not None:
            patch["actual_cost_usd"] = actual_cost_usd
        if cost_unknown is not None:
            patch["cost_unknown"] = cost_unknown
        if to_state in {OperationState.SUBMITTED, OperationState.SUBMIT_UNKNOWN}:
            patch["submitted_at"] = now.isoformat()
        if to_state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }:
            patch["completed_at"] = now.isoformat()

        params = {"id": f"eq.{operation_id}", "select": INFERENCE_OPERATION_COLUMNS}
        if expected_from is not None:
            states = ",".join(sorted(state.value for state in expected_from))
            params["state"] = f"in.({states})"
        response = self.client.request("PATCH", _PATH, params=params, json=patch)
        self._raise(response)
        rows = response.json()
        if not rows:
            # The optimistic condition matched no row. Distinguish "no such row"
            # from "wrong state" so callers can retry a reconciliation safely.
            if expected_from is not None:
                self.get(operation_id)  # raises OperationNotFoundError if absent
                raise TransitionConflictError(operation_id, frozenset(expected_from))
            raise OperationNotFoundError(str(operation_id))
        return self._from_row(rows[0])

    def save_resume_vars(
        self, operation_id: UUID, resume_vars: Mapping[str, Any]
    ) -> InferenceOperation:
        _reject_secret_values(resume_vars)
        patch = {
            "resume_vars": dict(resume_vars),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        response = self.client.request(
            "PATCH",
            _PATH,
            params={"id": f"eq.{operation_id}", "select": INFERENCE_OPERATION_COLUMNS},
            json=patch,
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise OperationNotFoundError(str(operation_id))
        return self._from_row(rows[0])

    def list_non_terminal_for_route(self, route_id: str) -> list[InferenceOperation]:
        response = self.client.request(
            "GET",
            _PATH,
            params={
                "provider_route_id": f"eq.{route_id}",
                "state": "in.(reserved,submitting,submitted,submit_unknown,running,needs_review)",
                "select": INFERENCE_OPERATION_COLUMNS,
                "order": "created_at.asc",
            },
        )
        self._raise(response)
        return [self._from_row(row) for row in response.json()]

    def count_by_state(self, state: OperationState) -> int:
        response = self.client.request(
            "GET",
            _PATH,
            params={"state": f"eq.{state.value}", "select": "id"},
            headers={"prefer": "count=exact"},
        )
        self._raise(response)
        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[1]
            if total.isdigit():
                return int(total)
        return len(response.json())

    @staticmethod
    def _is_unique_violation(response: httpx.Response) -> bool:
        if response.status_code < 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and payload.get("code") == _UNIQUE_VIOLATION

    @staticmethod
    def _from_row(row: dict[str, Any]) -> InferenceOperation:
        return InferenceOperation(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])) if row.get("project_id") else None,
            capability=str(row["capability"]),
            idempotency_key=str(row["idempotency_key"]),
            provider_route_id=str(row["provider_route_id"]),
            provider_model=str(row["provider_model"]),
            provider_model_revision=str(row["provider_model_revision"]),
            route_snapshot=row.get("route_snapshot") or {},
            state=OperationState(str(row["state"])),
            provider_operation_id=(
                str(row["provider_operation_id"]) if row.get("provider_operation_id") else None
            ),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_detail=str(row["error_detail"]) if row.get("error_detail") else None,
            estimated_cost_usd=(
                float(row["estimated_cost_usd"]) if row.get("estimated_cost_usd") is not None
                else None
            ),
            actual_cost_usd=(
                float(row["actual_cost_usd"]) if row.get("actual_cost_usd") is not None else None
            ),
            cost_unknown=bool(row.get("cost_unknown") or False),
            resume_vars=row.get("resume_vars") or {},
            created_at=_parse_ts(row["created_at"]),
            updated_at=_parse_ts(row["updated_at"]),
            submitted_at=_parse_ts(row["submitted_at"]) if row.get("submitted_at") else None,
            completed_at=_parse_ts(row["completed_at"]) if row.get("completed_at") else None,
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise InferenceOperationRepositoryError(
                f"inference operation store returned {response.status_code}"
            )


def _parse_ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
