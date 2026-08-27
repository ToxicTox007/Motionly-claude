from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.editorial_learning import (
    EditorialLearningDatasetResponse,
    EditorialLearningDeploymentResponse,
    EditorialLearningPolicyResponse,
)
from app.services.supabase import SupabaseRestClient


@dataclass
class EditorialLearningPolicyRecord:
    workspace_id: UUID
    user_id: UUID
    memory_enabled: bool = False
    training_enabled: bool = False
    policy_version: str = "unacknowledged"
    revision: int = 0
    retention_days: int = 90
    revoked_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> EditorialLearningPolicyResponse:
        return EditorialLearningPolicyResponse(**self.__dict__)


@dataclass(frozen=True)
class EditorialFeedbackDeltaRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    source_event_id: UUID | None
    layer: str
    normalizer_version: str
    delta: dict[str, object]
    content_hash: str
    policy_revision: int
    memory_eligible: bool
    training_eligible: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None


@dataclass
class EditorialTrainingDatasetRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    item_ids: tuple[UUID, ...]
    manifest_hash: str
    status: str = "frozen"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None

    def response(self) -> EditorialLearningDatasetResponse:
        return EditorialLearningDatasetResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            user_id=self.user_id,
            status=self.status,
            item_count=len(self.item_ids),
            manifest_hash=self.manifest_hash,
            created_at=self.created_at,
        )


@dataclass
class EditorialLearningDeploymentRecord:
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    active_dataset_id: UUID | None = None
    previous_dataset_id: UUID | None = None
    state: str = "disabled"
    revision: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> EditorialLearningDeploymentResponse:
        return EditorialLearningDeploymentResponse(**self.__dict__)


class InMemoryEditorialLearningRepository:
    """Consent and deployment ledger; missing state is intentionally deny-by-default."""

    def __init__(self, *, default_retention_days: int = 90) -> None:
        self.default_retention_days = default_retention_days
        self.policies: dict[tuple[UUID, UUID], EditorialLearningPolicyRecord] = {}
        self.deltas: dict[UUID, EditorialFeedbackDeltaRecord] = {}
        self.delta_by_source: dict[tuple[UUID, str], UUID] = {}
        self.datasets: dict[UUID, EditorialTrainingDatasetRecord] = {}
        self.deployments: dict[tuple[UUID, UUID, UUID], EditorialLearningDeploymentRecord] = {}
        self.policy_events: list[dict[str, object]] = []
        self._lock = RLock()

    def get_policy_or_default(
        self, *, workspace_id: UUID, user_id: UUID
    ) -> EditorialLearningPolicyRecord:
        return self.policies.get(
            (workspace_id, user_id),
            EditorialLearningPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                retention_days=self.default_retention_days,
            ),
        )

    def set_policy_if_revision(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        memory_enabled: bool,
        training_enabled: bool,
        policy_version: str,
        retention_days: int | None,
        expected_revision: int | None,
    ) -> EditorialLearningPolicyRecord:
        with self._lock:
            current = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
            if expected_revision is not None and expected_revision != current.revision:
                raise ApiError(
                    409,
                    "editorial_learning_policy_stale",
                    "Reload editorial learning settings before saving.",
                )
            next_record = EditorialLearningPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                memory_enabled=memory_enabled,
                training_enabled=training_enabled,
                policy_version=policy_version,
                revision=current.revision + 1,
                retention_days=retention_days or current.retention_days,
                revoked_at=None,
            )
            self.policies[(workspace_id, user_id)] = next_record
            self.policy_events.append(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "revision": next_record.revision,
                    "event": "policy_set",
                    "at": next_record.updated_at,
                }
            )
            return next_record

    def capture_delta(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        source_event_id: UUID | None,
        layer: str,
        delta: dict[str, object],
        normalizer_version: str,
        memory_eligible: bool,
        training_eligible: bool,
    ) -> EditorialFeedbackDeltaRecord | None:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if policy.revoked_at or not (policy.memory_enabled or policy.training_enabled):
            return None
        key = (source_event_id, normalizer_version) if source_event_id else None
        with self._lock:
            if key is not None and key in self.delta_by_source:
                return self.deltas[self.delta_by_source[key]]
            encoded = json.dumps(delta, sort_keys=True, separators=(",", ":"))
            record = EditorialFeedbackDeltaRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
                source_event_id=source_event_id,
                layer=layer,
                normalizer_version=normalizer_version,
                delta=delta,
                content_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                policy_revision=policy.revision,
                memory_eligible=memory_eligible and policy.memory_enabled,
                training_eligible=training_eligible and policy.training_enabled,
            )
            self.deltas[record.id] = record
            if key is not None:
                self.delta_by_source[key] = record.id
            return record

    def freeze_dataset(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, minimum_examples: int
    ) -> EditorialTrainingDatasetRecord:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if not policy.training_enabled or policy.revoked_at:
            raise ApiError(
                403,
                "training_consent_required",
                "Enable editorial training before creating a training dataset.",
            )
        records = tuple(
            sorted(
                (
                    item
                    for item in self.deltas.values()
                    if item.workspace_id == workspace_id
                    and item.project_id == project_id
                    and item.user_id == user_id
                    and item.training_eligible
                    and item.revoked_at is None
                ),
                key=lambda item: (item.created_at, str(item.id)),
            )
        )
        items = tuple(item.id for item in records)
        if len(items) < minimum_examples:
            raise ApiError(
                409,
                "training_examples_insufficient",
                f"At least {minimum_examples} consented edit deltas are required.",
            )
        manifest_hash = hashlib.sha256(":".join(map(str, items)).encode()).hexdigest()
        record = EditorialTrainingDatasetRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            item_ids=items,
            manifest_hash=manifest_hash,
        )
        self.datasets[record.id] = record
        return record

    def get_dataset(
        self, *, dataset_id: UUID, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> EditorialTrainingDatasetRecord:
        record = self.datasets.get(dataset_id)
        if record is None or (record.workspace_id, record.project_id, record.user_id) != (
            workspace_id,
            project_id,
            user_id,
        ):
            raise ApiError(
                404, "editorial_dataset_not_found", "Editorial training dataset was not found."
            )
        if record.status == "revoked" or record.revoked_at:
            raise ApiError(
                409, "training_dataset_revoked", "This editorial dataset has been revoked."
            )
        return record

    def list_datasets(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, limit: int
    ) -> list[EditorialTrainingDatasetRecord]:
        datasets = [
            dataset
            for dataset in self.datasets.values()
            if (dataset.workspace_id, dataset.project_id, dataset.user_id)
            == (workspace_id, project_id, user_id)
        ]
        return sorted(datasets, key=lambda dataset: dataset.created_at, reverse=True)[:limit]

    def get_deployment_or_default(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> EditorialLearningDeploymentRecord:
        return self.deployments.get(
            (workspace_id, project_id, user_id),
            EditorialLearningDeploymentRecord(
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
            ),
        )

    def dataset_deltas(
        self, *, dataset: EditorialTrainingDatasetRecord
    ) -> list[EditorialFeedbackDeltaRecord]:
        return [self.deltas[item_id] for item_id in dataset.item_ids if item_id in self.deltas]

    def resolve_active_memory(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> dict[str, object] | None:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        deployment = self.deployments.get((workspace_id, project_id, user_id))
        if (
            not policy.memory_enabled
            or policy.revoked_at
            or not deployment
            or deployment.state != "active"
            or not deployment.active_dataset_id
        ):
            return None
        dataset = self.get_dataset(
            dataset_id=deployment.active_dataset_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
        )
        return {
            "dataset_id": str(dataset.id),
            "manifest_hash": dataset.manifest_hash,
            "policy_revision": policy.revision,
        }

    def promote(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        dataset_id: UUID,
        expected_revision: int,
    ) -> EditorialLearningDeploymentRecord:
        self.get_dataset(
            dataset_id=dataset_id, workspace_id=workspace_id, project_id=project_id, user_id=user_id
        )
        key = (workspace_id, project_id, user_id)
        with self._lock:
            current = self.deployments.get(key, EditorialLearningDeploymentRecord(*key))
            if current.revision != expected_revision:
                raise ApiError(
                    409,
                    "editorial_deployment_stale",
                    "Editorial deployment changed; reload before promoting.",
                )
            current.previous_dataset_id = current.active_dataset_id
            current.active_dataset_id = dataset_id
            current.state = "active"
            current.revision += 1
            current.updated_at = datetime.now(UTC)
            self.deployments[key] = current
            return current

    def rollback(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, reason: str
    ) -> EditorialLearningDeploymentRecord:
        key = (workspace_id, project_id, user_id)
        with self._lock:
            current = self.deployments.get(key, EditorialLearningDeploymentRecord(*key))
            current.active_dataset_id = current.previous_dataset_id
            current.previous_dataset_id = None
            current.state = "active" if current.active_dataset_id else "disabled"
            current.revision += 1
            current.updated_at = datetime.now(UTC)
            self.deployments[key] = current
            self.policy_events.append(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "event": "deployment_rollback",
                    "reason": reason,
                    "at": current.updated_at,
                }
            )
            return current

    def revoke(
        self, *, workspace_id: UUID, user_id: UUID, memory: bool, training: bool, reason: str
    ) -> EditorialLearningPolicyRecord:
        with self._lock:
            current = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
            policy = EditorialLearningPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                memory_enabled=current.memory_enabled and not memory,
                training_enabled=current.training_enabled and not training,
                policy_version=current.policy_version,
                revision=current.revision + 1,
                retention_days=current.retention_days,
                revoked_at=datetime.now(UTC),
            )
            self.policies[(workspace_id, user_id)] = policy
            for identifier, delta in list(self.deltas.items()):
                if delta.workspace_id == workspace_id and delta.user_id == user_id:
                    self.deltas[identifier] = EditorialFeedbackDeltaRecord(
                        **{
                            **delta.__dict__,
                            "revoked_at": policy.revoked_at,
                            "memory_eligible": False,
                            "training_eligible": False,
                        }
                    )
            for dataset in self.datasets.values():
                if dataset.workspace_id == workspace_id and dataset.user_id == user_id:
                    dataset.status = "revoked"
                    dataset.revoked_at = policy.revoked_at
                    self.rollback(
                        workspace_id=workspace_id,
                        project_id=dataset.project_id,
                        user_id=user_id,
                        reason="policy_revoked",
                    )
            self.policy_events.append(
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "revision": policy.revision,
                    "event": "revoked",
                    "reason": reason,
                    "at": policy.revoked_at,
                }
            )
            return policy


POLICY_COLUMNS = (
    "workspace_id,user_id,memory_enabled,training_enabled,policy_version,revision,"
    "retention_days,revoked_at,updated_at"
)
DELTA_COLUMNS = (
    "id,workspace_id,project_id,user_id,source_event_id,layer,normalizer_version,delta,"
    "content_hash,policy_revision,memory_eligible,training_eligible,created_at,revoked_at"
)
DATASET_COLUMNS = "id,workspace_id,project_id,user_id,status,manifest_hash,created_at,revoked_at"
DEPLOYMENT_COLUMNS = (
    "workspace_id,project_id,user_id,active_dataset_id,previous_dataset_id,state,revision,"
    "updated_at"
)


class SupabaseEditorialLearningRepository:
    """Durable, service-role-only editorial governance repository.

    Consent changes, normalized feedback admission, dataset freezing, and deployment
    changes are each delegated to database RPCs. This keeps compare-and-swap and
    revoke operations atomic while REST reads always include workspace/project/user
    ownership filters and explicit projections.
    """

    def __init__(
        self, *, supabase_url: str, service_role_key: str, default_retention_days: int = 90
    ) -> None:
        self.default_retention_days = default_retention_days
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="editorial_learning",
        )

    def get_policy_or_default(
        self, *, workspace_id: UUID, user_id: UUID
    ) -> EditorialLearningPolicyRecord:
        rows = self._get_rows(
            "editorial_learning_policies",
            {
                "workspace_id": f"eq.{workspace_id}",
                "user_id": f"eq.{user_id}",
                "select": POLICY_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            return EditorialLearningPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                retention_days=self.default_retention_days,
            )
        return self._policy_from_row(rows[0])

    def set_policy_if_revision(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        memory_enabled: bool,
        training_enabled: bool,
        policy_version: str,
        retention_days: int | None,
        expected_revision: int | None,
    ) -> EditorialLearningPolicyRecord:
        row = self._rpc_single(
            "motionly_set_editorial_learning_policy",
            {
                "p_workspace_id": str(workspace_id),
                "p_user_id": str(user_id),
                "p_memory_enabled": memory_enabled,
                "p_training_enabled": training_enabled,
                "p_policy_version": policy_version,
                "p_retention_days": retention_days,
                "p_expected_revision": expected_revision,
            },
        )
        return self._policy_from_row(row)

    def capture_delta(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        source_event_id: UUID | None,
        layer: str,
        delta: dict[str, object],
        normalizer_version: str,
        memory_eligible: bool,
        training_eligible: bool,
    ) -> EditorialFeedbackDeltaRecord | None:
        encoded = json.dumps(delta, sort_keys=True, separators=(",", ":"))
        rows = self._rpc_rows(
            "motionly_capture_editorial_feedback_delta",
            {
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_user_id": str(user_id),
                "p_source_event_id": str(source_event_id) if source_event_id else None,
                "p_layer": layer,
                "p_delta": delta,
                "p_normalizer_version": normalizer_version,
                "p_content_hash": hashlib.sha256(encoded.encode()).hexdigest(),
                "p_memory_eligible": memory_eligible,
                "p_training_eligible": training_eligible,
            },
        )
        return self._delta_from_row(rows[0]) if rows else None

    def freeze_dataset(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, minimum_examples: int
    ) -> EditorialTrainingDatasetRecord:
        row = self._rpc_single(
            "motionly_freeze_editorial_training_dataset",
            {
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_user_id": str(user_id),
                "p_minimum_examples": minimum_examples,
            },
        )
        return self._dataset_from_row(row)

    def get_dataset(
        self, *, dataset_id: UUID, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> EditorialTrainingDatasetRecord:
        rows = self._get_rows(
            "editorial_training_datasets",
            {
                "id": f"eq.{dataset_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "user_id": f"eq.{user_id}",
                "select": DATASET_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            raise ApiError(
                404, "editorial_dataset_not_found", "Editorial training dataset was not found."
            )
        dataset = self._dataset_from_row(rows[0])
        if dataset.status == "revoked" or dataset.revoked_at:
            raise ApiError(
                409, "training_dataset_revoked", "This editorial dataset has been revoked."
            )
        return dataset

    def list_datasets(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, limit: int
    ) -> list[EditorialTrainingDatasetRecord]:
        rows = self._get_rows(
            "editorial_training_datasets",
            {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "user_id": f"eq.{user_id}",
                "select": DATASET_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._dataset_from_row(row) for row in rows]

    def get_deployment_or_default(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> EditorialLearningDeploymentRecord:
        rows = self._get_rows(
            "editorial_learning_deployments",
            {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "user_id": f"eq.{user_id}",
                "select": DEPLOYMENT_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            return EditorialLearningDeploymentRecord(
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
            )
        return self._deployment_from_row(rows[0])

    def dataset_deltas(
        self, *, dataset: EditorialTrainingDatasetRecord
    ) -> list[EditorialFeedbackDeltaRecord]:
        if not dataset.item_ids:
            return []
        ids = ",".join(str(item_id) for item_id in dataset.item_ids)
        rows = self._get_rows(
            "editorial_feedback_deltas",
            {
                "id": f"in.({ids})",
                "workspace_id": f"eq.{dataset.workspace_id}",
                "project_id": f"eq.{dataset.project_id}",
                "user_id": f"eq.{dataset.user_id}",
                "revoked_at": "is.null",
                "select": DELTA_COLUMNS,
                "limit": str(len(dataset.item_ids)),
            },
        )
        by_id = {record.id: record for record in map(self._delta_from_row, rows)}
        return [by_id[item_id] for item_id in dataset.item_ids if item_id in by_id]

    def resolve_active_memory(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID
    ) -> dict[str, object] | None:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if not policy.memory_enabled or policy.revoked_at:
            return None
        rows = self._get_rows(
            "editorial_learning_deployments",
            {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "user_id": f"eq.{user_id}",
                "state": "eq.active",
                "select": DEPLOYMENT_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            return None
        deployment = self._deployment_from_row(rows[0])
        if not deployment.active_dataset_id:
            return None
        try:
            dataset = self.get_dataset(
                dataset_id=deployment.active_dataset_id,
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
            )
        except ApiError:
            return None
        return {
            "dataset_id": str(dataset.id),
            "manifest_hash": dataset.manifest_hash,
            "policy_revision": policy.revision,
        }

    def promote(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        dataset_id: UUID,
        expected_revision: int,
    ) -> EditorialLearningDeploymentRecord:
        row = self._rpc_single(
            "motionly_promote_editorial_learning_dataset",
            {
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_user_id": str(user_id),
                "p_dataset_id": str(dataset_id),
                "p_expected_revision": expected_revision,
            },
        )
        return self._deployment_from_row(row)

    def rollback(
        self, *, workspace_id: UUID, project_id: UUID, user_id: UUID, reason: str
    ) -> EditorialLearningDeploymentRecord:
        row = self._rpc_single(
            "motionly_rollback_editorial_learning_deployment",
            {
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_user_id": str(user_id),
                "p_reason": reason,
            },
        )
        return self._deployment_from_row(row)

    def revoke(
        self, *, workspace_id: UUID, user_id: UUID, memory: bool, training: bool, reason: str
    ) -> EditorialLearningPolicyRecord:
        if not memory and not training:
            raise ApiError(422, "editorial_scope_required", "Select memory or training to revoke.")
        scope = "all" if memory and training else "memory" if memory else "training"
        self._rpc_rows(
            "motionly_revoke_editorial_learning",
            {
                "p_workspace_id": str(workspace_id),
                "p_user_id": str(user_id),
                "p_scope": scope,
                "p_reason": reason,
            },
        )
        return self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)

    def _dataset_from_row(self, row: dict[str, Any]) -> EditorialTrainingDatasetRecord:
        dataset_id = UUID(str(row["id"]))
        workspace_id = UUID(str(row["workspace_id"]))
        project_id = UUID(str(row["project_id"]))
        user_id = UUID(str(row["user_id"]))
        item_rows = self._get_rows(
            "editorial_training_dataset_items",
            {
                "dataset_id": f"eq.{dataset_id}",
                "select": "delta_id",
                "order": "delta_id.asc",
                "limit": "10000",
            },
        )
        return EditorialTrainingDatasetRecord(
            id=dataset_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            item_ids=tuple(UUID(str(item["delta_id"])) for item in item_rows),
            manifest_hash=str(row["manifest_hash"]),
            status=str(row.get("status") or "frozen"),
            created_at=self._datetime(row.get("created_at")),
            revoked_at=self._optional_datetime(row.get("revoked_at")),
        )

    @staticmethod
    def _policy_from_row(row: dict[str, Any]) -> EditorialLearningPolicyRecord:
        return EditorialLearningPolicyRecord(
            workspace_id=UUID(str(row["workspace_id"])),
            user_id=UUID(str(row["user_id"])),
            memory_enabled=bool(row.get("memory_enabled")),
            training_enabled=bool(row.get("training_enabled")),
            policy_version=str(row.get("policy_version") or "unacknowledged"),
            revision=int(row.get("revision") or 0),
            retention_days=int(row.get("retention_days") or 90),
            revoked_at=SupabaseEditorialLearningRepository._optional_datetime(
                row.get("revoked_at")
            ),
            updated_at=SupabaseEditorialLearningRepository._datetime(row.get("updated_at")),
        )

    @staticmethod
    def _delta_from_row(row: dict[str, Any]) -> EditorialFeedbackDeltaRecord:
        raw_delta = row.get("delta")
        return EditorialFeedbackDeltaRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            user_id=UUID(str(row["user_id"])),
            source_event_id=(
                UUID(str(row["source_event_id"])) if row.get("source_event_id") else None
            ),
            layer=str(row["layer"]),
            normalizer_version=str(row["normalizer_version"]),
            delta=raw_delta if isinstance(raw_delta, dict) else {},
            content_hash=str(row["content_hash"]),
            policy_revision=int(row["policy_revision"]),
            memory_eligible=bool(row.get("memory_eligible")),
            training_eligible=bool(row.get("training_eligible")),
            created_at=SupabaseEditorialLearningRepository._datetime(row.get("created_at")),
            revoked_at=SupabaseEditorialLearningRepository._optional_datetime(
                row.get("revoked_at")
            ),
        )

    @staticmethod
    def _deployment_from_row(row: dict[str, Any]) -> EditorialLearningDeploymentRecord:
        return EditorialLearningDeploymentRecord(
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            user_id=UUID(str(row["user_id"])),
            active_dataset_id=(
                UUID(str(row["active_dataset_id"])) if row.get("active_dataset_id") else None
            ),
            previous_dataset_id=(
                UUID(str(row["previous_dataset_id"])) if row.get("previous_dataset_id") else None
            ),
            state=str(row.get("state") or "disabled"),
            revision=int(row.get("revision") or 0),
            updated_at=SupabaseEditorialLearningRepository._datetime(row.get("updated_at")),
        )

    def _get_rows(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self.client.request("GET", f"/{table}", params=params)
        self._raise(response)
        return self._response_rows(response)

    def _rpc_single(self, function: str, payload: dict[str, object]) -> dict[str, Any]:
        rows = self._rpc_rows(function, payload)
        if not rows:
            raise ApiError(
                502,
                "editorial_learning_repository_failed",
                "Editorial governance did not return the required record.",
            )
        return rows[0]

    def _rpc_rows(self, function: str, payload: dict[str, object]) -> list[dict[str, Any]]:
        response = self.client.request("POST", f"/rpc/{function}", json=payload)
        self._raise(response)
        return self._response_rows(response)

    @staticmethod
    def _response_rows(response: httpx.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError:
            return []
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return [payload] if isinstance(payload, dict) else []

    @staticmethod
    def _datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return datetime.now(UTC)

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        if value is None or value == "":
            return None
        return SupabaseEditorialLearningRepository._datetime(value)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = (
            str(payload.get("message") or payload.get("details") or payload.get("hint") or "")
            if isinstance(payload, dict)
            else ""
        )
        text = f"{message} {getattr(response, 'text', '')}".upper()
        known = {
            "EDITORIAL_POLICY_STALE": (
                409,
                "editorial_learning_policy_stale",
                "Reload editorial learning settings before saving.",
            ),
            "EDITORIAL_DEPLOYMENT_STALE": (
                409,
                "editorial_deployment_stale",
                "Editorial deployment changed; reload before promoting.",
            ),
            "TRAINING_CONSENT_REQUIRED": (
                403,
                "training_consent_required",
                "Enable editorial training before creating a training dataset.",
            ),
            "TRAINING_EXAMPLES_INSUFFICIENT": (
                409,
                "training_examples_insufficient",
                "More consented edit deltas are required.",
            ),
            "EDITORIAL_DATASET_NOT_FOUND": (
                404,
                "editorial_dataset_not_found",
                "Editorial training dataset was not found.",
            ),
            "EDITORIAL_DATASET_REVOKED": (
                409,
                "training_dataset_revoked",
                "This editorial dataset has been revoked.",
            ),
            "EDITORIAL_MEMORY_CONSENT_REQUIRED": (
                403,
                "memory_consent_required",
                "Enable editorial memory before promoting a dataset.",
            ),
        }
        for marker, error in known.items():
            if marker in text:
                raise ApiError(*error)
        raise ApiError(
            502,
            "editorial_learning_repository_failed",
            "Editorial governance data could not be persisted.",
        )
