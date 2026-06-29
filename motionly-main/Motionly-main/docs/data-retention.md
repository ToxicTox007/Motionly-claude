# Data retention policy

The maintenance worker runs retention daily (default `0 3 * * *`, configurable with `RETENTION_CRON`). Deletion is idempotent and removes stored objects before their database records.

| Data | Default retention | Configuration |
|---|---:|---|
| Source video | 365 days | `RETENTION_SOURCE_DAYS` |
| Generated B-roll and separated stems | 90 days | `RETENTION_GENERATED_ASSET_DAYS` |
| Transcripts and word timing | 365 days | `RETENTION_TRANSCRIPT_DAYS` |
| AI edit/analytics events | 365 days | `RETENTION_ANALYTICS_DAYS` |
| Encoded exports | 30 days | `RETENTION_EXPORT_DAYS` |
| Local processing cache | 7 days | `RETENTION_LOCAL_CACHE_DAYS` |

Manual project/workspace deletion cascades database records immediately. Source/export object paths are transactionally copied to `storage_deletion_queue` before those records disappear; the next maintenance run removes the objects. The same sweep provides the time-based lifecycle above.
