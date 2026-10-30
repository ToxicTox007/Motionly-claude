import time
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

# Deletion queue drains claim batches until the queue is empty or a bounded
# budget is spent (audit WJ-14: one 100-row claim per drain capped throughput
# far below the rate rows are queued).
DRAIN_BATCH_SIZE = 100
DRAIN_MAX_BATCHES = 50
DRAIN_TIME_BUDGET_SECONDS = 600
EXPORT_PAGE_SIZE = 200
EXPORT_MAX_PAGES = 25
REDACTION_BATCH_SIZE = 500
REDACTION_MAX_BATCHES = 20
# A 'running' retention row older than this belongs to a run that died.
RETENTION_RUN_STALE_SECONDS = 6 * 3600


class RetentionAlreadyRunning(Exception):
    """Another retention run is live; this one must not start (audit WJ-11)."""


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
        self.failed_steps: list[str] = []

    def run_scheduled(self) -> dict[str, int]:
        """Run retention once, recorded in ``retention_runs``.

        At most one run is live at a time: a concurrent call (for example a
        BullMQ retry after the worker's HTTP call timed out while this run kept
        going) raises :class:`RetentionAlreadyRunning`. A run whose steps failed
        is recorded as ``failed`` with the failed step names, never as
        ``completed`` (audit WJ-11/WJ-14).
        """
        if not self.rest_url or not self.settings.supabase_service_role_key:
            return self.run()
        run_id = self._begin_run()
        if run_id is None:
            raise RetentionAlreadyRunning()
        try:
            counts = self.run()
            failed_steps = list(self.failed_steps)
            self._request(
                "PATCH",
                "retention_runs",
                params={"id": f"eq.{run_id}"},
                json={
                    "status": "failed" if failed_steps else "completed",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "deleted_counts": counts,
                    "error_message": (
                        ("steps failed: " + ", ".join(failed_steps))[:500]
                        if failed_steps
                        else None
                    ),
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

    def _begin_run(self) -> str | None:
        result = self._request(
            "POST",
            "rpc/motionly_begin_retention_run",
            json={"p_stale_after_seconds": RETENTION_RUN_STALE_SECONDS},
        )
        if isinstance(result, list):
            result = result[0] if result else None
        if isinstance(result, dict):
            result = result.get("motionly_begin_retention_run") or result.get("id")
        return str(result) if result else None

    def run(self) -> dict[str, int]:
        self.failed_steps = []
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
            # Each cleanup step runs independently (B19): a failure in one is
            # logged and isolated so it cannot stop the steps that follow.
            # Expiry releases reservations, closes active upload rows, and queues
            # every temporary part. Drain those part objects without the normal
            # long retention delay.
            counts["expired_upload_sessions"] = self._run_step(
                "expire_upload_sessions", self._expire_upload_sessions
            )
            counts["expired_generic_media_upload_sessions"] = self._run_step(
                "expire_generic_media_upload_sessions",
                self._expire_generic_media_upload_sessions,
            )
            counts["expired_storage_reservations"] = self._run_step(
                "expire_storage_quota_reservations",
                self._expire_storage_quota_reservations,
            )
            for source_table in (
                "upload_session_parts",
                "upload_sessions",
                "generic_media_upload_session_parts",
                "generic_media_upload_sessions",
                "generic_media_deletion",
                "legacy_upload_expiry",
                "legacy_upload_cancellation",
            ):
                counts["queued_storage_objects"] += self._run_step(
                    f"drain_storage_deletion_queue:{source_table}",
                    lambda source_table=source_table: self._drain_storage_deletion_queue(
                        source_table=source_table,
                        minimum_age_days=None,
                        minimum_age_seconds=SIGNED_UPLOAD_CLEANUP_GRACE_SECONDS,
                    ),
                )

            # Drain other deleted objects queued more than 30 days ago.
            counts["queued_storage_objects"] += self._run_step(
                "drain_storage_deletion_queue:aged", self._drain_storage_deletion_queue
            )

            # Delete exports older than retention_export_days (90 days)
            counts["exports"] = self._run_step(
                "delete_exports",
                lambda: self._delete_media_rows(
                    table="exports",
                    older_than_days=self.settings.retention_export_days,
                ),
            )
            # Redact raw provider payloads in transcripts older than 30 days. The
            # RPC keeps derived audio intelligence (planning reads it) and skips
            # already-redacted rows instead of rewriting every old transcript
            # daily (audit WJ-15).
            counts["transcripts"] = self._run_step(
                "redact_transcripts", self._redact_transcript_payloads
            )

            counts["queued_storage_objects"] += self._run_step(
                "drain_storage_deletion_queue:final", self._drain_storage_deletion_queue
            )

        counts["local_cache_files"], counts["local_cache_bytes"] = self._run_step(
            "evict_local_cache",
            self._evict_local_cache_counts,
            default=(0, 0),
        )
        return counts

    def _evict_local_cache_counts(self) -> tuple[int, int]:
        evicted = self._evict_local_cache()
        return evicted["removed_files"], evicted["removed_bytes"]

    def _run_step(self, name, step, *, default=0):
        """Run one cleanup step in isolation (B19).

        A failure in one step is logged and isolated so it cannot prevent the
        remaining steps from running. The step name is collected in
        ``failed_steps`` so the scheduled run is recorded as failed.
        """
        try:
            return step()
        except Exception as exc:  # noqa: BLE001 - steps are independent by design
            self.failed_steps.append(name)
            logger.warning(
                "retention_step_failed",
                extra={"step": name, "error": type(exc).__name__},
            )
            return default

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
        processed = 0
        deadline = time.monotonic() + DRAIN_TIME_BUDGET_SECONDS
        for _batch in range(DRAIN_MAX_BATCHES):
            rows = self._claim_storage_deletion_batch(source_table=source_table, before=before)
            processed += self._process_storage_deletion_batch(rows)
            if len(rows) < DRAIN_BATCH_SIZE or time.monotonic() >= deadline:
                break
        return processed

    def _claim_storage_deletion_batch(
        self, *, source_table: str | None, before: str | None
    ) -> list[dict[str, object]]:
        rows = self._request(
            "POST",
            "rpc/motionly_claim_storage_deletion_queue",
            json={
                "p_lease_owner": self._queue_lease_owner,
                "p_limit": DRAIN_BATCH_SIZE,
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
        return rows

    def _process_storage_deletion_batch(self, rows: list[dict[str, object]]) -> int:
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
                # leaves its leased row in place for fenced retry. The claim RPC
                # counts attempts and dead-letters a row that keeps failing
                # (WJ-14); the error code is kept on the row for operators.
                error_code = exc.code if isinstance(exc, ApiError) else type(exc).__name__
                logger.warning(
                    "retention_storage_deletion_failed",
                    extra={
                        "queue_id": str(row.get("id")),
                        "error": error_code,
                        "attempt_count": row.get("attempt_count"),
                    },
                )
                self._note_storage_deletion_failure(row, lease_token, error_code)
        return processed

    def _note_storage_deletion_failure(
        self, row: dict[str, object], lease_token: str, error_code: str
    ) -> None:
        try:
            self._request(
                "POST",
                "rpc/motionly_note_storage_deletion_failure",
                json={
                    "p_queue_id": str(row["id"]),
                    "p_lease_token": lease_token,
                    "p_error_code": str(error_code)[:120],
                },
            )
        except Exception:  # noqa: BLE001 - the note is diagnostic only
            logger.warning("retention_failure_note_failed", extra={"queue_id": str(row.get("id"))})

    def _redact_transcript_payloads(self) -> int:
        cutoff = self._cutoff(30)
        redacted = 0
        for _batch in range(REDACTION_MAX_BATCHES):
            result = self._request(
                "POST",
                "rpc/motionly_redact_transcript_raw_payloads",
                json={"p_before": cutoff, "p_limit": REDACTION_BATCH_SIZE},
            )
            batch = self._scalar_int(result, "motionly_redact_transcript_raw_payloads")
            redacted += batch
            if batch < REDACTION_BATCH_SIZE:
                break
        return redacted

    @staticmethod
    def _scalar_int(result: object, function_name: str) -> int:
        if isinstance(result, bool):
            return 0
        if isinstance(result, int):
            return result
        if isinstance(result, list) and result:
            value = result[0]
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, dict) and value.get(function_name) is not None:
                return int(value[function_name])
        return 0

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
        """Delete expired media rows and their objects, a page at a time.

        Each row is isolated: a failed object or row delete is logged and left
        for the next run instead of aborting the rest of the step (audit WJ-14).
        Successful deletes leave the result set, so failures become the offset.
        """
        cutoff = self._cutoff(older_than_days)
        deleted = 0
        failures = 0
        for _page in range(EXPORT_MAX_PAGES):
            rows = self._request(
                "GET",
                table,
                params={
                    "created_at": f"lt.{cutoff}",
                    "select": "id,storage_bucket,storage_path",
                    "order": "created_at.asc,id.asc",
                    "limit": str(EXPORT_PAGE_SIZE),
                    "offset": str(failures),
                },
            )
            for row in rows:
                try:
                    self.storage_signer.delete_object(
                        bucket=str(row["storage_bucket"]), path=str(row["storage_path"])
                    )
                    self._request("DELETE", table, params={"id": f"eq.{row['id']}"})
                    deleted += 1
                except Exception as exc:  # noqa: BLE001 - one row must not stop the rest
                    failures += 1
                    logger.warning(
                        "retention_media_row_delete_failed",
                        extra={"table": table, "row_id": str(row.get("id")),
                               "error": type(exc).__name__},
                    )
            if len(rows) < EXPORT_PAGE_SIZE:
                break
        if failures:
            # Keep the successful count, but record the run as not clean.
            self.failed_steps.append(f"delete_{table}:partial")
        return deleted

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
