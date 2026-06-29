import hashlib
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx


@dataclass
class WorkerHeartbeat:
    worker_id: str
    worker_role: str
    queues: list[str]
    version: str | None
    metadata: dict[str, Any]
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class OperationsRepository:
    def __init__(self, *, supabase_url: str | None = None, service_role_key: str | None = None):
        self.base_url = f"{supabase_url.rstrip('/')}/rest/v1" if supabase_url else None
        self.headers = (
            {
                "apikey": service_role_key,
                "authorization": f"Bearer {service_role_key}",
                "content-type": "application/json",
            }
            if service_role_key
            else None
        )
        self.heartbeats: dict[str, WorkerHeartbeat] = {}
        self.provider_events: list[dict[str, Any]] = []
        self.cost_events: list[dict[str, Any]] = []

    def heartbeat(self, **values: Any) -> WorkerHeartbeat:
        record = WorkerHeartbeat(**values)
        self.heartbeats[record.worker_id] = record
        if self.base_url and self.headers:
            response = httpx.post(
                f"{self.base_url}/worker_heartbeats?on_conflict=worker_id",
                headers={**self.headers, "prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "worker_id": record.worker_id,
                    "worker_role": record.worker_role,
                    "queues": record.queues,
                    "version": record.version,
                    "metadata": record.metadata,
                    "last_seen_at": record.last_seen_at.isoformat(),
                },
                timeout=5,
            )
            response.raise_for_status()
        return record

    def latest_heartbeats(self) -> list[WorkerHeartbeat]:
        if self.base_url and self.headers:
            response = httpx.get(
                f"{self.base_url}/worker_heartbeats",
                headers=self.headers,
                params={"select": "*", "order": "last_seen_at.desc", "limit": "100"},
                timeout=5,
            )
            response.raise_for_status()
            return [
                WorkerHeartbeat(
                    worker_id=row["worker_id"],
                    worker_role=row["worker_role"],
                    queues=row.get("queues") or [],
                    version=row.get("version"),
                    metadata=row.get("metadata") or {},
                    last_seen_at=datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00")),
                )
                for row in response.json()
            ]
        return list(self.heartbeats.values())

    def record_provider_slo(self, **values: Any) -> None:
        self.provider_events.append(values)
        self._insert("provider_slo_events", values)

    def record_cost(self, **values: Any) -> None:
        if "source_id" not in values:
            identity = "|".join(
                str(values.get(key) or "")
                for key in ("render_job_id", "project_id", "provider", "category", "metadata")
            )
            values["source_id"] = hashlib.sha256(identity.encode()).hexdigest()[:32]
        if any(
            row.get("workspace_id") == values.get("workspace_id")
            and row.get("provider") == values.get("provider")
            and row.get("category") == values.get("category")
            and row.get("source_id") == values.get("source_id")
            for row in self.cost_events
        ):
            return
        self.cost_events.append(values)
        self._insert("cost_attribution_events", values, upsert=True)

    def count_usage(
        self,
        *,
        workspace_id: Any,
        provider: str,
        category: str,
        period_start: datetime,
        period_end: datetime,
    ) -> float:
        if not self.base_url or not self.headers:
            total = 0.0
            for row in self.cost_events:
                if (
                    str(row.get("workspace_id")) == str(workspace_id)
                    and row.get("provider") == provider
                    and row.get("category") == category
                ):
                    created_at = row.get("created_at") or datetime.now(UTC)
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        except Exception:
                            continue
                    if period_start <= created_at < period_end:
                        total += float(row.get("quantity") or 0.0)
            return total

        # Query PostgREST
        start_str = period_start.isoformat()
        end_str = period_end.isoformat()
        params = [
            ("workspace_id", f"eq.{workspace_id}"),
            ("provider", f"eq.{provider}"),
            ("category", f"eq.{category}"),
            ("created_at", f"gte.{start_str}"),
            ("created_at", f"lt.{end_str}"),
            ("select", "quantity"),
            ("limit", "10000"),
        ]
        try:
            response = httpx.get(
                f"{self.base_url}/cost_attribution_events",
                headers=self.headers,
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json()
            return sum(float(row.get("quantity") or 0.0) for row in rows)
        except Exception:
            total = 0.0
            for row in self.cost_events:
                if (
                    str(row.get("workspace_id")) == str(workspace_id)
                    and row.get("provider") == provider
                    and row.get("category") == category
                ):
                    created_at = row.get("created_at") or datetime.now(UTC)
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        except Exception:
                            continue
                    if period_start <= created_at < period_end:
                        total += float(row.get("quantity") or 0.0)
            return total

    def cost_summary(self, *, render_job_id=None) -> dict[str, Any]:
        rows = self._cost_rows(render_job_id=render_job_id)
        by_category: dict[str, float] = defaultdict(float)
        approved_exports: set[str] = set()
        total = 0.0
        for row in rows:
            cost = float(row["quantity"]) * float(row["unit_cost_usd"])
            total += cost
            by_category[str(row["category"])] += cost
            if row.get("approved_at") and row.get("export_id"):
                approved_exports.add(str(row["export_id"]))
        return {
            "currency": "usd",
            "total_usd": round(total, 6),
            "by_category": {key: round(value, 6) for key, value in by_category.items()},
            "approved_exports": len(approved_exports),
            "cost_per_approved_export_usd": (
                round(total / len(approved_exports), 6) if approved_exports else None
            ),
        }

    def approve_export(self, *, workspace_id, render_job_id, export_id) -> None:
        approved_at = datetime.now(UTC)
        for row in self.cost_events:
            if str(row.get("workspace_id")) == str(workspace_id) and str(
                row.get("render_job_id")
            ) == str(render_job_id):
                row["export_id"] = export_id
                row["approved_at"] = approved_at
        if self.base_url and self.headers:
            response = httpx.patch(
                f"{self.base_url}/cost_attribution_events",
                headers={**self.headers, "prefer": "return=minimal"},
                params={
                    "workspace_id": f"eq.{workspace_id}",
                    "render_job_id": f"eq.{render_job_id}",
                },
                json={"export_id": str(export_id), "approved_at": approved_at.isoformat()},
                timeout=5,
            )
            response.raise_for_status()

    def _cost_rows(self, *, render_job_id=None) -> list[dict[str, Any]]:
        if not self.base_url or not self.headers:
            return [
                row
                for row in self.cost_events
                if render_job_id is None or str(row.get("render_job_id")) == str(render_job_id)
            ]
        params = {"select": "*", "limit": "10000"}
        if render_job_id is not None:
            params["render_job_id"] = f"eq.{render_job_id}"
        response = httpx.get(
            f"{self.base_url}/cost_attribution_events",
            headers=self.headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def _insert(self, table: str, values: dict[str, Any], *, upsert: bool = False) -> None:
        if not self.base_url or not self.headers:
            return
        payload = {key: _json_value(value) for key, value in values.items()}
        suffix = "?on_conflict=workspace_id,provider,category,source_id" if upsert else ""
        preference = "resolution=ignore-duplicates,return=minimal" if upsert else "return=minimal"
        response = httpx.post(
            f"{self.base_url}/{table}{suffix}",
            headers={**self.headers, "prefer": preference},
            json=payload,
            timeout=5,
        )
        response.raise_for_status()


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._latency_sum: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()

    def observe(self, name: str, value: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += 1
            self._latency_sum[key] += value

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), count in sorted(self._counters.items()):
                suffix = (
                    "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}"
                    if labels
                    else ""
                )
                lines.append(f"motionly_{name}_count{suffix} {count}")
                lines.append(f"motionly_{name}_sum{suffix} {self._latency_sum[(name, labels)]:.6f}")
        return "\n".join(lines) + "\n"


def monotonic_ms() -> int:
    return round(time.monotonic() * 1000)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex"):
        return str(value)
    return value
