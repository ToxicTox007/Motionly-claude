from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.services.local_storage import LocalObjectStorage
from app.services.storage import StorageSigner

logger = get_logger(__name__)

SIGNED_UPLOAD_CLEANUP_GRACE_SECONDS = StorageSigner.SUPABASE_SIGNED_UPLOAD_TTL_SECONDS + 5 * 60


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
        # A service instance owns its queue leases. A crashed instance cannot
        # acknowledge work after its bounded database lease is reclaimed.
        self._queue_lease_owner = f"retention:{uuid4()}"

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
            "expired_upload_sessions": 0,
            "expired_generic_media_upload_sessions": 0,
            "expired_storage_reservations": 0,
            "queued_storage_objects": 0,
        }
        if self.rest_url and self.settings.supabase_service_role_key:
            # Expiry releases reservations, closes active upload rows, and queues
            # every temporary part. Drain those part objects without the normal
            # long retention delay.
            counts["expired_upload_sessions"] = self._expire_upload_sessions()
            counts["expired_generic_media_upload_sessions"] = (
                self._expire_generic_media_upload_sessions()
            )
            counts["expired_storage_reservations"] = self._expire_storage_quota_reservations()
            for source_table in (
                "upload_session_parts",
                "upload_sessions",
                "generic_media_upload_session_parts",
                "generic_media_upload_sessions",
                "generic_media_deletion",
                "legacy_upload_expiry",
                "legacy_upload_cancellation",
            ):
                counts["queued_storage_objects"] += self._drain_storage_deletion_queue(
                    source_table=source_table,
                    minimum_age_days=None,
                    minimum_age_seconds=SIGNED_UPLOAD_CLEANUP_GRACE_SECONDS,
                )

            # Drain other deleted objects queued more than 30 days ago.
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

    def _expire_upload_sessions(self) -> int:
        return self._rpc_count("motionly_expire_upload_sessions")

    def _expire_generic_media_upload_sessions(self) -> int:
        return self._rpc_count("motionly_expire_generic_media_upload_sessions")

    def _expire_storage_quota_reservations(self) -> int:
        return self._rpc_count("motionly_expire_storage_quota_reservations")

    def _rpc_count(self, function_name: str) -> int:
        result = self._request("POST", f"rpc/{function_name}", json={})
        if isinstance(result, int):
            return result
        if isinstance(result, list) and result:
            value = result[0]
            if isinstance(value, int):
                return value
            if isinstance(value, dict):
                scalar = value.get(function_name)
                if scalar is not None:
                    return int(scalar)
        return 0

    def _drain_storage_deletion_queue(
        self,
        *,
        source_table: str | None = None,
        minimum_age_days: int | None = 30,
        minimum_age_seconds: int | None = None,
    ) -> int:
        before = None
        if minimum_age_seconds is not None:
            before = self._cutoff_seconds(minimum_age_seconds)
        elif minimum_age_days is not None:
            before = self._cutoff(minimum_age_days)
        rows = self._request(
            "POST",
            "rpc/motionly_claim_storage_deletion_queue",
            json={
                "p_lease_owner": self._queue_lease_owner,
                "p_limit": 100,
                "p_source_table": source_table,
                "p_before": before,
                "p_lease_seconds": 300,
            },
        )
        if not isinstance(rows, list):
            raise ApiError(
                502,
                "retention_claim_invalid",
                "Retention queue claim returned an invalid response.",
            )
        processed = 0
        for row in rows:
            lease_token = row.get("lease_token")
            if not isinstance(lease_token, str) or not lease_token:
                raise ApiError(
                    502,
                    "retention_claim_invalid",
                    "Retention queue claim omitted its fencing token.",
                )
            try:
                self.storage_signer.delete_object(
                    bucket=str(row["storage_bucket"]), path=str(row["storage_path"])
                )
                generic_deletion_job_id = row.get("generic_media_deletion_job_id")
                if generic_deletion_job_id is not None:
                    acknowledged = self._request(
                        "POST",
                        "rpc/motionly_ack_generic_media_deletion_object",
                        json={"p_queue_id": str(row["id"]), "p_lease_token": lease_token},
                    )
                    if not self._rpc_boolean(
                        acknowledged,
                        "motionly_ack_generic_media_deletion_object",
                    ):
                        # The final object can be physically deleted before a claimed
                        # part is acknowledged. Leave its durable row retryable; the
                        # acknowledgement RPC prevents any early quota transition.
                        logger.info(
                            "retention_generic_media_ack_deferred",
                            extra={
                                "queue_id": str(row["id"]),
                                "job_id": str(generic_deletion_job_id),
                            },
                        )
                        continue
                else:
                    reservation_id = row.get("quota_reservation_id")
                    if reservation_id is not None:
                        released = self._request(
                            "POST",
                            "rpc/motionly_delete_committed_storage_quota",
                            json={"p_reservation_id": str(reservation_id)},
                        )
                        if not self._rpc_boolean(
                            released,
                            "motionly_delete_committed_storage_quota",
                        ):
                            raise ApiError(
                                502,
                                "retention_quota_cleanup_failed",
                                "Deleted storage quota could not be reconciled.",
                            )
                    acknowledged = self._request(
                        "POST",
                        "rpc/motionly_ack_storage_deletion_queue",
                        json={"p_queue_id": str(row["id"]), "p_lease_token": lease_token},
                    )
                    if not self._rpc_boolean(acknowledged, "motionly_ack_storage_deletion_queue"):
                        logger.info(
                            "retention_claim_stale",
                            extra={"queue_id": str(row["id"])},
                        )
                        continue
                processed += 1
            except Exception as exc:
                # A failed external delete, quota transition, or acknowledgement
                # leaves its leased row in place for fenced retry. Continue with
                # unrelated queue rows instead of abandoning the whole retention run.
                logger.warning(
                    "retention_storage_deletion_failed",
                    extra={"queue_id": str(row.get("id")), "error": type(exc).__name__},
                )
        return processed

    @staticmethod
    def _rpc_boolean(result: object, function_name: str) -> bool:
        if isinstance(result, bool):
            return result
        if isinstance(result, list) and len(result) == 1:
            value = result[0]
            if isinstance(value, bool):
                return value
            if isinstance(value, dict):
                return bool(value.get(function_name))
        return False

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
    def _cutoff_seconds(seconds: int) -> str:
        return (datetime.now(UTC) - timedelta(seconds=max(1, seconds))).isoformat()

    @staticmethod
    def _cutoff(days: int) -> str:
        return (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()
