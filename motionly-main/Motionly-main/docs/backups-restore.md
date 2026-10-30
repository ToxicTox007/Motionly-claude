# Backup and restore runbook

Motionly uses Supabase Postgres as the source of truth. Production keeps continuous WAL archiving
and daily base backups (point-in-time recovery for 30 days, copies retained 35 days in the backups
bucket) plus daily data-volume snapshots. [Database operations](ops/database-operations.md)
describes the backup layers, their alarms and the point-in-time recovery procedure. Test restore
before launch and after any destructive schema class is proposed.

## Required restore drill

1. Describe the source and an empty throwaway target as libpq services in a `pg_service.conf`, with
   their passwords in a `pgpass` file, both mode `0600`; export `PGSERVICEFILE` and `PGPASSFILE`.
2. Run `MOTIONLY_BACKUP_RESTORE_ACK=restore-target scripts/verify_backup_restore.sh <source-service>
<restore-service>`. It dumps with `pg_dump --format=custom --no-owner`, loads the dump with
   `pg_restore --no-owner` including the application schemas' grants, and fails unless table data
   fingerprints, API-role privileges, row-level security and policies all match the source.
3. Run migrations/schema tests against the restored database.
4. Run repository integration contracts against the restored database.
5. Record the drill timestamp, source backup ID, restore target, and test results in the launch
   checklist.

The CI gate runs `scripts/verify_backup_restore.sh` against local Supabase to prove the dump/restore
path is executable with the current schema.
