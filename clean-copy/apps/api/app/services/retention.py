from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.api.errors import ApiError
from app.services.local_storage import LocalObjectStorage


class RetentionService:
    def __init__(self, *, settings, storage_signer) -> None:
        self.settings = settings
        self.storage_signer = storage_signer
        self.rest_url = (
            f"{str(settings.supabase_url).rstrip('/')}/rest/v1" if settings.supabase_url else None
        )
        self.headers = {
            "apikey": settings.supabase_service_role_key or "",
            "authorization": f"Bearer {settings.supabase_service_role_key or ''}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def run_scheduled(self) -> dict[str, int]:
        if not self.rest_url or not self.settings.supabase_service_role_key:
            return self.run()
        started = self._request(
            "POST", "retention_runs", json={"status": "running", "deleted_counts": {}}
        )
        run_id = str(started[0]["id"])
        try:
            counts = self.run()
            self._request(
                "PATCH",
                "retention_runs",
                params={"id": f"eq.{run_id}"},
                json={
                    "status": "completed",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "deleted_counts": counts,
                    "error_message": None,
                },
            )
            return counts
        except Exception as exc:
            self._request(
                "PATCH",
                "retention_runs",
                params={"id": f"eq.{run_id}"},
                json={
                    "status": "failed",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "error_message": type(exc).__name__,
                },
            )
            raise

    def run(self) -> dict[str, int]:
        counts = {
            "source_videos": 0,
            "generated_assets": 0,
            "transcripts": 0,
            "analytics_events": 0,
            "exports": 0,
            "local_cache_files": 0,
            "local_cache_bytes": 0,
            "queued_storage_objects": 0,
        }
        if self.rest_url and self.settings.supabase_service_role_key:
            # Drain deleted objects queued more than 30 days ago
            counts["queued_storage_objects"] += self._drain_storage_deletion_queue()
            
            # Delete exports older than retention_export_days (90 days)
            counts["exports"] = self._delete_media_rows(
                table="exports",
                older_than_days=self.settings.retention_export_days,
            )
            
            # Redact/expire raw provider payloads in transcripts older than 30 days
            cutoff_30 = self._cutoff(30)
            self._request(
                "PATCH",
                "transcripts",
                params={"created_at": f"lt.{cutoff_30}"},
                json={"raw_payload": {}},
            )
            
            counts["queued_storage_objects"] += self._drain_storage_deletion_queue()
            
        evicted = self._evict_local_cache()
        counts["local_cache_files"] = evicted["removed_files"]
        counts["local_cache_bytes"] = evicted["removed_bytes"]
        return counts

    def _drain_storage_deletion_queue(self) -> int:
        cutoff = self._cutoff(30) # Only delete objects queued for 30+ days
        rows = self._request(
            "GET",
            "storage_deletion_queue",
            params={
                "queued_at": f"lt.{cutoff}",
                "select": "id,storage_bucket,storage_path",
                "order": "queued_at.asc"
            },
        )
        for row in rows:
            self.storage_signer.delete_object(
                bucket=str(row["storage_bucket"]), path=str(row["storage_path"])
            )
            self._request(
                "DELETE", "storage_deletion_queue", params={"id": f"eq.{row['id']}"}
            )
        return len(rows)

    def _delete_media_rows(self, *, table: str, older_than_days: int) -> int:
        cutoff = self._cutoff(older_than_days)
        rows = self._request(
            "GET",
            table,
            params={"created_at": f"lt.{cutoff}", "select": "id,storage_bucket,storage_path"},
        )
        for row in rows:
            self.storage_signer.delete_object(
                bucket=str(row["storage_bucket"]), path=str(row["storage_path"])
            )
            self._request("DELETE", table, params={"id": f"eq.{row['id']}"})
        return len(rows)

    def _delete_rows(
        self,
        *,
        table: str,
        timestamp_column: str,
        older_than_days: int,
        extra_filter: str | None = None,
    ) -> int:
        params = {timestamp_column: f"lt.{self._cutoff(older_than_days)}", "select": "id"}
        if extra_filter:
            key, value = extra_filter.split("=", 1)
            params[key] = value
        rows = self._request("GET", table, params=params)
        for row in rows:
            self._request("DELETE", table, params={"id": f"eq.{row['id']}"})
        return len(rows)

    def _evict_local_cache(self) -> dict[str, int]:
        root = Path(self.settings.media_work_dir)
        if not root.exists():
            return {"removed_files": 0, "removed_bytes": 0}
        evicted = LocalObjectStorage(root=root).evict_cache(
            max_bytes=getattr(self.settings, "media_cache_max_bytes", 10_737_418_240),
            max_age_seconds=self.settings.retention_local_cache_days * 86400,
        )
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return evicted

    def _request(self, method: str, table: str, **kwargs: object) -> list[dict[str, object]]:
        response = httpx.request(
            method, f"{self.rest_url}/{table}", headers=self.headers, timeout=30, **kwargs
        )
        if response.status_code >= 400:
            raise ApiError(502, "retention_failed", f"Retention failed for {table}.")
        if not response.content:
            return []
        return response.json()

    @staticmethod
    def _cutoff(days: int) -> str:
        return (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()
