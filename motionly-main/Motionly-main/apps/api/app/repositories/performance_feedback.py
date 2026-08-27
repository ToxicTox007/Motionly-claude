from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.schemas.performance_feedback import (
    DurationBucket,
    PerformanceFeedbackPolicyResponse,
    PerformanceMetricResponse,
)
from app.schemas.publishing import PublishPlatform
from app.services.supabase import SupabaseRestClient

POLICY_COLUMNS = (
    "workspace_id,user_id,metrics_ingestion_enabled,suggestions_enabled,policy_version,revision,"
    "retention_days,revoked_at,updated_at"
)
METRIC_COLUMNS = (
    "id,workspace_id,project_id,user_id,publish_job_id,platform,observed_at,views,"
    "average_view_duration_seconds,completion_rate,engagement_rate,caption_preset,hook_style,"
    "duration_bucket,duration_seconds,template_id,captions_enabled,policy_revision,"
    "suggestion_eligible,expires_at,created_at"
)


@dataclass
class PerformanceFeedbackPolicyRecord:
    workspace_id: UUID
    user_id: UUID
    metrics_ingestion_enabled: bool = False
    suggestions_enabled: bool = False
    policy_version: str = "unacknowledged"
    revision: int = 0
    retention_days: int = 90
    revoked_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> PerformanceFeedbackPolicyResponse:
        return PerformanceFeedbackPolicyResponse(**self.__dict__)


@dataclass(frozen=True)
class PerformanceMetricRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    publish_job_id: UUID
    platform: PublishPlatform
    observed_at: datetime
    views: int | None
    average_view_duration_seconds: float | None
    completion_rate: float | None
    engagement_rate: float | None
    caption_preset: str | None
    hook_style: str | None
    duration_bucket: DurationBucket
    duration_seconds: float | None
    template_id: str | None
    captions_enabled: bool
    policy_revision: int
    suggestion_eligible: bool
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> PerformanceMetricResponse:
        return PerformanceMetricResponse(
            id=self.id,
            project_id=self.project_id,
            publish_job_id=self.publish_job_id,
            platform=self.platform,
            observed_at=self.observed_at,
            expires_at=self.expires_at,
            suggestion_eligible=self.suggestion_eligible,
            created_at=self.created_at,
        )


class InMemoryPerformanceFeedbackRepository:
    """Per-user consent ledger. No policy means no metric admission or suggestions."""

    def __init__(self, *, default_retention_days: int = 90) -> None:
        self.default_retention_days = default_retention_days
        self.policies: dict[tuple[UUID, UUID], PerformanceFeedbackPolicyRecord] = {}
        self.metrics: dict[UUID, PerformanceMetricRecord] = {}
        self.metric_by_snapshot: dict[tuple[UUID, datetime], UUID] = {}
        self.policy_events: list[dict[str, object]] = []
        self._lock = RLock()

    def get_policy_or_default(
        self, *, workspace_id: UUID, user_id: UUID
    ) -> PerformanceFeedbackPolicyRecord:
        return self.policies.get(
            (workspace_id, user_id),
            PerformanceFeedbackPolicyRecord(
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
        metrics_ingestion_enabled: bool,
        suggestions_enabled: bool,
        policy_version: str,
        retention_days: int | None,
        expected_revision: int | None,
    ) -> PerformanceFeedbackPolicyRecord:
        with self._lock:
            current = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
            if expected_revision is not None and expected_revision != current.revision:
                raise ApiError(
                    409,
                    "performance_feedback_policy_stale",
                    "Reload performance-feedback settings before saving.",
                )
            policy = PerformanceFeedbackPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                metrics_ingestion_enabled=metrics_ingestion_enabled,
                suggestions_enabled=suggestions_enabled,
                policy_version=policy_version,
                revision=current.revision + 1,
                retention_days=retention_days or current.retention_days,
            )
            self.policies[(workspace_id, user_id)] = policy
            self.policy_events.append(
                {
                    "event": "policy_set",
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "at": policy.updated_at,
                }
            )
            return policy

    def capture_metric(self, **values: object) -> PerformanceMetricRecord | None:
        workspace_id = values["workspace_id"]
        user_id = values["user_id"]
        if not isinstance(workspace_id, UUID) or not isinstance(user_id, UUID):
            raise TypeError("workspace_id and user_id must be UUID values")
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if policy.revoked_at or not policy.metrics_ingestion_enabled:
            return None
        observed_at = values["observed_at"]
        publish_job_id = values["publish_job_id"]
        if not isinstance(observed_at, datetime) or not isinstance(publish_job_id, UUID):
            raise TypeError("publish_job_id and observed_at are required")
        key = (publish_job_id, observed_at)
        with self._lock:
            existing = self.metric_by_snapshot.get(key)
            if existing:
                return self.metrics[existing]
            record = PerformanceMetricRecord(
                id=uuid4(),
                policy_revision=policy.revision,
                suggestion_eligible=policy.suggestions_enabled,
                expires_at=datetime.now(UTC) + timedelta(days=policy.retention_days),
                **values,
            )
            self.metrics[record.id] = record
            self.metric_by_snapshot[key] = record.id
            return record

    def list_eligible_metrics(
        self, *, workspace_id: UUID, user_id: UUID, platform: PublishPlatform | None = None
    ) -> list[PerformanceMetricRecord]:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if policy.revoked_at or not policy.suggestions_enabled:
            return []
        now = datetime.now(UTC)
        return sorted(
            [
                metric
                for metric in self.metrics.values()
                if metric.workspace_id == workspace_id
                and metric.user_id == user_id
                and metric.suggestion_eligible
                and metric.expires_at > now
                and (platform is None or metric.platform == platform)
            ],
            key=lambda metric: (metric.observed_at, metric.created_at),
            reverse=True,
        )

    def revoke(
        self, *, workspace_id: UUID, user_id: UUID, reason: str
    ) -> PerformanceFeedbackPolicyRecord:
        with self._lock:
            current = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
            revoked_at = datetime.now(UTC)
            policy = PerformanceFeedbackPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                policy_version=current.policy_version,
                revision=current.revision + 1,
                retention_days=current.retention_days,
                revoked_at=revoked_at,
            )
            self.policies[(workspace_id, user_id)] = policy
            for metric_id, metric in list(self.metrics.items()):
                if metric.workspace_id == workspace_id and metric.user_id == user_id:
                    self.metrics.pop(metric_id)
                    self.metric_by_snapshot.pop((metric.publish_job_id, metric.observed_at), None)
            self.policy_events.append(
                {
                    "event": "revoked_and_purged",
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "reason": reason,
                    "at": revoked_at,
                }
            )
            return policy


class SupabasePerformanceFeedbackRepository:
    """Service-role repository backed by scoped reads and atomic, consent-gated RPCs."""

    def __init__(
        self, *, supabase_url: str, service_role_key: str, default_retention_days: int = 90
    ) -> None:
        self.default_retention_days = default_retention_days
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="performance_feedback",
        )

    def get_policy_or_default(
        self, *, workspace_id: UUID, user_id: UUID
    ) -> PerformanceFeedbackPolicyRecord:
        rows = self._rows(
            "GET",
            "/performance_feedback_policies",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "user_id": f"eq.{user_id}",
                "select": POLICY_COLUMNS,
                "limit": "1",
            },
        )
        return (
            self._policy(rows[0])
            if rows
            else PerformanceFeedbackPolicyRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                retention_days=self.default_retention_days,
            )
        )

    def set_policy_if_revision(self, **values: object) -> PerformanceFeedbackPolicyRecord:
        row = self._rpc_single(
            "motionly_set_performance_feedback_policy",
            {
                "p_workspace_id": str(values["workspace_id"]),
                "p_user_id": str(values["user_id"]),
                "p_metrics_ingestion_enabled": values["metrics_ingestion_enabled"],
                "p_suggestions_enabled": values["suggestions_enabled"],
                "p_policy_version": values["policy_version"],
                "p_retention_days": values["retention_days"],
                "p_expected_revision": values["expected_revision"],
            },
        )
        return self._policy(row)

    def capture_metric(self, **values: object) -> PerformanceMetricRecord | None:
        rows = self._rows(
            "POST",
            "/rpc/motionly_capture_performance_metric",
            json={
                "p_workspace_id": str(values["workspace_id"]),
                "p_project_id": str(values["project_id"]),
                "p_user_id": str(values["user_id"]),
                "p_publish_job_id": str(values["publish_job_id"]),
                "p_platform": str(values["platform"]),
                "p_observed_at": self._json_value(values["observed_at"]),
                "p_views": values["views"],
                "p_average_view_duration_seconds": values["average_view_duration_seconds"],
                "p_completion_rate": values["completion_rate"],
                "p_engagement_rate": values["engagement_rate"],
                "p_caption_preset": values["caption_preset"],
                "p_hook_style": values["hook_style"],
                "p_duration_bucket": str(values["duration_bucket"]),
                "p_duration_seconds": values["duration_seconds"],
                "p_template_id": values["template_id"],
                "p_captions_enabled": values["captions_enabled"],
            },
        )
        return self._metric(rows[0]) if rows else None

    def list_eligible_metrics(
        self, *, workspace_id: UUID, user_id: UUID, platform: PublishPlatform | None = None
    ) -> list[PerformanceMetricRecord]:
        policy = self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)
        if policy.revoked_at or not policy.suggestions_enabled:
            return []
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "user_id": f"eq.{user_id}",
            "suggestion_eligible": "eq.true",
            "expires_at": f"gt.{datetime.now(UTC).isoformat()}",
            "select": METRIC_COLUMNS,
            "order": "observed_at.desc",
            "limit": "500",
        }
        if platform:
            params["platform"] = f"eq.{platform.value}"
        return [
            self._metric(row)
            for row in self._rows("GET", "/performance_feedback_metrics", params=params)
        ]

    def revoke(
        self, *, workspace_id: UUID, user_id: UUID, reason: str
    ) -> PerformanceFeedbackPolicyRecord:
        self._rows(
            "POST",
            "/rpc/motionly_revoke_performance_feedback",
            json={
                "p_workspace_id": str(workspace_id),
                "p_user_id": str(user_id),
                "p_reason": reason,
            },
        )
        return self.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)

    def _rpc_single(self, function: str, payload: dict[str, object]) -> dict[str, Any]:
        rows = self._rows("POST", f"/rpc/{function}", json=payload)
        if not rows:
            raise ApiError(
                502,
                "performance_feedback_repository_failed",
                "Performance-feedback policy was not saved.",
            )
        return rows[0]

    def _rows(self, method: str, path: str, **kwargs: object) -> list[dict[str, Any]]:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(
                502,
                "performance_feedback_repository_failed",
                "Performance-feedback data is unavailable.",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                502,
                "performance_feedback_repository_failed",
                "Performance-feedback data is invalid.",
            ) from exc
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _policy(row: dict[str, Any]) -> PerformanceFeedbackPolicyRecord:
        return PerformanceFeedbackPolicyRecord(
            workspace_id=UUID(str(row["workspace_id"])),
            user_id=UUID(str(row["user_id"])),
            metrics_ingestion_enabled=bool(row.get("metrics_ingestion_enabled")),
            suggestions_enabled=bool(row.get("suggestions_enabled")),
            policy_version=str(row.get("policy_version") or "unacknowledged"),
            revision=int(row.get("revision") or 0),
            retention_days=int(row.get("retention_days") or 90),
            revoked_at=SupabasePerformanceFeedbackRepository._optional_datetime(
                row.get("revoked_at")
            ),
            updated_at=SupabasePerformanceFeedbackRepository._datetime(row.get("updated_at")),
        )

    @staticmethod
    def _metric(row: dict[str, Any]) -> PerformanceMetricRecord:
        return PerformanceMetricRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            user_id=UUID(str(row["user_id"])),
            publish_job_id=UUID(str(row["publish_job_id"])),
            platform=PublishPlatform(str(row["platform"])),
            observed_at=SupabasePerformanceFeedbackRepository._datetime(row.get("observed_at")),
            views=int(row["views"]) if row.get("views") is not None else None,
            average_view_duration_seconds=(
                float(row["average_view_duration_seconds"])
                if row.get("average_view_duration_seconds") is not None
                else None
            ),
            completion_rate=float(row["completion_rate"])
            if row.get("completion_rate") is not None
            else None,
            engagement_rate=float(row["engagement_rate"])
            if row.get("engagement_rate") is not None
            else None,
            caption_preset=str(row["caption_preset"]) if row.get("caption_preset") else None,
            hook_style=str(row["hook_style"]) if row.get("hook_style") else None,
            duration_bucket=DurationBucket(str(row.get("duration_bucket") or "unknown")),
            duration_seconds=float(row["duration_seconds"])
            if row.get("duration_seconds") is not None
            else None,
            template_id=str(row["template_id"]) if row.get("template_id") else None,
            captions_enabled=bool(row.get("captions_enabled")),
            policy_revision=int(row["policy_revision"]),
            suggestion_eligible=bool(row.get("suggestion_eligible")),
            expires_at=SupabasePerformanceFeedbackRepository._datetime(row.get("expires_at")),
            created_at=SupabasePerformanceFeedbackRepository._datetime(row.get("created_at")),
        )

    @staticmethod
    def _datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        return None if value is None else SupabasePerformanceFeedbackRepository._datetime(value)

    @staticmethod
    def _json_value(value: object) -> object:
        return value.isoformat() if isinstance(value, datetime) else value
