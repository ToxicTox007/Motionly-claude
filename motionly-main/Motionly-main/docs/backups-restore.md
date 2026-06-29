# Backup and restore runbook

Motionly uses Supabase Postgres as the source of truth. Production must keep automated daily
database backups, retain at least 30 days, and test restore before launch and after any destructive
schema class is proposed.

## Required restore drill

1. Export from the source database with `pg_dump --format=custom --no-owner --no-acl`.
2. Restore into an empty throwaway database with `pg_restore --clean --if-exists --no-owner`.
3. Run migrations/schema tests against the restored database.
4. Run repository integration contracts against the restored database.
5. Record the drill timestamp, source backup ID, restore target, and test results in the launch
   checklist.

The CI gate runs `scripts/verify_backup_restore.sh` against local Supabase to prove the dump/restore
path is executable with the current schema.
