import hashlib
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

WORKER_HEARTBEAT_COLUMNS = "worker_id,worker_role,queues,version,metadata,last_seen_at"
COST_ATTRIBUTION_SUMMARY_COLUMNS = (
    "workspace_id,render_job_id,export_id,provider,category,quantity,unit_cost_usd,approved_at"
)
PROVIDER_SLO_SUMMARY_COLUMNS = (
    "provider,operation,success,latency_ms,error_code,model,model_revision,route_id,"
    "request_id,fallback_count,estimated_cost_usd,actual_cost_usd,usage,created_at"
)


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

    @staticmethod
    def _is_missing_relation(error: Exception) -> bool:
        if not isinstance(error, httpx.HTTPStatusError):
            return False
        response = error.response
        if response.status_code != 404:
            return False
        try:
            payload = response.json()
        except Exception:
            return False
        code = str(payload.get("code") or "")
        message = str(payload.get("message") or "")
        normalized = message.lower()
        return (
            code in {"42P01", "PGRST205"}
            or "relation" in normalized
            or "schema cache" in normalized
        )

    def heartbeat(self, **values: Any) -> WorkerHeartbeat:
        record = WorkerHeartbeat(**values)
        self.heartbeats[record.worker_id] = record
        if self.base_url and self.headers:
            try:
                response = httpx.post(
                    f"{self.base_url}/worker_heartbeats?on_conflict=worker_id",
                    headers={
                        **self.headers,
                        "prefer": "resolution=merge-duplicates,return=minimal",
                    },
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
            except Exception as exc:
                if not self._is_missing_relation(exc):
                    raise
        return record

    def latest_heartbeats(self) -> list[WorkerHeartbeat]:
        if self.base_url and self.headers:
            try:
                response = httpx.get(
                    f"{self.base_url}/worker_heartbeats",
                    headers=self.headers,
                    params={
                        "select": WORKER_HEARTBEAT_COLUMNS,
                        "order": "last_seen_at.desc",
                        "limit": "100",
                    },
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
                        last_seen_at=datetime.fromisoformat(
                            row["last_seen_at"].replace("Z", "+00:00")
                        ),
                    )
                    for row in response.json()
                ]
            except Exception as exc:
                if not self._is_missing_relation(exc):
                    raise
        return list(self.heartbeats.values())

    def record_provider_slo(self, **values: Any) -> None:
        self.provider_events.append(values)
        self._insert("provider_slo_events", values)

    def provider_slo_summary(self, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._provider_slo_rows(limit=limit)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            provider = str(row.get("provider") or "unknown")
            operation = str(row.get("operation") or "unknown")
            grouped[(provider, operation)].append(row)

        summary: list[dict[str, Any]] = []
        for (provider, operation), events in sorted(grouped.items()):
            latencies = sorted(int(event.get("latency_ms") or 0) for event in events)
            success_count = sum(1 for event in events if bool(event.get("success")))
            error_count = len(events) - success_count
            p95_index = max(0, min(len(latencies) - 1, int((len(latencies) - 1) * 0.95)))
            last_error_code = next(
                (
                    str(event.get("error_code"))
                    for event in sorted(
                        events,
                        key=lambda item: _datetime_value(item.get("created_at")),
                        reverse=True,
                    )
                    if event.get("error_code")
                ),
                None,
            )
            latest_event = max(
                events,
                key=lambda item: _datetime_value(item.get("created_at")),
            )
            summary.append(
                {
                    "provider": provider,
                    "operation": operation,
                    "total": len(events),
                    "successes": success_count,
                    "errors": error_count,
                    "success_rate": round(success_count / len(events), 4) if events else None,
                    "avg_latency_ms": (
                        round(sum(latencies) / len(latencies), 2) if latencies else None
                    ),
                    "p95_latency_ms": latencies[p95_index] if latencies else None,
                    "last_error_code": last_error_code,
                    "model": latest_event.get("model"),
                    "model_revision": latest_event.get("model_revision"),
                    "route_id": latest_event.get("route_id"),
                    "last_actual_cost_usd": latest_event.get("actual_cost_usd"),
                    "last_fallback_count": latest_event.get("fallback_count", 0),
                }
            )
        return summary

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
        by_provider: dict[str, float] = defaultdict(float)
        approved_exports: set[str] = set()
        total = 0.0
        for row in rows:
            cost = float(row["quantity"]) * float(row["unit_cost_usd"])
            total += cost
            by_category[str(row["category"])] += cost
            by_provider[str(row["provider"])] += cost
            if row.get("approved_at") and row.get("export_id"):
                approved_exports.add(str(row["export_id"]))
        return {
            "currency": "usd",
            "total_usd": round(total, 6),
            "by_category": {key: round(value, 6) for key, value in by_category.items()},
            "by_provider": {key: round(value, 6) for key, value in by_provider.items()},
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
        params = {"select": COST_ATTRIBUTION_SUMMARY_COLUMNS, "limit": "10000"}
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

    def _provider_slo_rows(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if not self.base_url or not self.headers:
            return sorted(
                self.provider_events,
                key=lambda row: _datetime_value(row.get("created_at")),
                reverse=True,
            )[:limit]
        response = httpx.get(
            f"{self.base_url}/provider_slo_events",
            headers=self.headers,
            params={
                "select": PROVIDER_SLO_SUMMARY_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
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
    def __init__(self, *, max_samples_per_metric: int = 2_000) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._latency_sum: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._samples: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = (
            defaultdict(list)
        )
        self._max_samples_per_metric = max_samples_per_metric

    def observe(self, name: str, value: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += 1
            self._latency_sum[key] += value
            samples = self._samples[key]
            samples.append(float(value))
            if len(samples) > self._max_samples_per_metric:
                del samples[: len(samples) - self._max_samples_per_metric]

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

    def summary(self) -> dict[str, Any]:
        with self._lock:
            samples = {key: list(values) for key, values in self._samples.items()}
        api_samples: list[float] = []
        api_errors_by_route: Counter[str] = Counter()
        supabase_samples: list[float] = []
        slow_supabase_calls = 0
        render_samples: list[float] = []
        export_samples: list[float] = []
        for (name, labels_tuple), values in samples.items():
            labels = dict(labels_tuple)
            if name == "http_request_duration_ms":
                api_samples.extend(values)
                status = labels.get("status", "")
                if status.startswith("5"):
                    api_errors_by_route[labels.get("route", "unknown")] += len(values)
            elif name == "supabase_request_duration_ms":
                supabase_samples.extend(values)
                slow_supabase_calls += len([value for value in values if value >= 1_000])
            elif name in {"render_latency_ms", "render_duration_ms", "render_job_duration_ms"}:
                render_samples.extend(values)
            elif name in {"export_latency_ms", "export_duration_ms", "export_job_duration_ms"}:
                export_samples.extend(values)
        return {
            "api": {
                "p95_ms": _percentile(api_samples, 95),
                "p99_ms": _percentile(api_samples, 99),
                "route_errors_5xx": sum(api_errors_by_route.values()),
                "route_errors_by_route": dict(api_errors_by_route),
            },
            "supabase": {
                "p95_ms": _percentile(supabase_samples, 95),
                "slow_call_count": slow_supabase_calls,
            },
            "render": {"p95_latency_ms": _percentile(render_samples, 95)},
            "export": {"p95_latency_ms": _percentile(export_samples, 95)},
        }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


def monotonic_ms() -> int:
    return round(time.monotonic() * 1000)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex"):
        return str(value)
    return value


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)
