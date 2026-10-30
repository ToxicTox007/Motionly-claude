# PostgreSQL 15 → 17 logical upgrade runbook

`supabase/config.toml` and production Compose target PostgreSQL 17.4.1.069. Do **not** mount a PostgreSQL 15 data directory into PostgreSQL 17 or attempt an in-place binary upgrade inside the Compose volume. The approved path is a rehearsed logical `pg_dump`/`pg_restore` migration with explicit boundaries.

## Rehearsal (no production cutover)

1. Provision an empty PostgreSQL 17 target with the same required extensions and roles. Confirm the source is PostgreSQL 15 and target is PostgreSQL 17. The scripts take named libpq services, never database URLs, so passwords stay out of process listings and shell history. Describe both databases in a `pg_service.conf` and their passwords in a `pgpass` file, both mode `0600`:

```sh
umask 077
mkdir -p "$HOME/.motionly"
cat > "$HOME/.motionly/pg_service.conf" <<'EOF'
[motionly_pg15_source]
host=<pg15-host>
port=5432
dbname=postgres
user=postgres

[motionly_pg17_target]
host=<pg17-host>
port=5432
dbname=postgres
user=postgres
EOF
# pgpass lines: host:port:database:user:password
export PGSERVICEFILE="$HOME/.motionly/pg_service.conf" PGPASSFILE="$HOME/.motionly/pgpass"

MOTIONLY_POSTGRES17_UPGRADE_ACK=pg15-to-pg17 \
  scripts/deploy/rehearse_postgres17_upgrade.sh motionly_pg15_source motionly_pg17_target
```

The script refuses other major versions and delegates to `scripts/verify_backup_restore.sh`, which refuses a non-empty restore target, fingerprints public-table data, compares every API role's table and function privileges plus row-level security policies between source and target, and proves the restored target is writable. Save its report (`MOTIONLY_BACKUP_RESTORE_REPORT_PATH`) with the release evidence.

2. Restore the rehearsal dump into a disposable target and run `supabase db reset`/`supabase test db` plus application repository contracts against PostgreSQL 17. Resolve every migration, extension, role, and query-plan difference before scheduling cutover.

## Production cutover

1. Freeze writes and drain workers. Record the current digest manifest and preserve the PostgreSQL 15 volume untouched.
2. Take a final logical dump and restore it to a **new, empty** PostgreSQL 17 volume/target. Run the rehearsal verifier, migrations, API health, and representative authenticated read/write checks before ingress cutover.
3. Switch the approved Compose release only after acceptance. Keep the PostgreSQL 15 volume and prior image manifest intact until the agreed observation period ends.

## Rollback boundary

Rollback is safe only before accepting PostgreSQL 17 writes: stop the 17 stack, reattach the preserved PostgreSQL 15 volume, and activate the prior Compose release manifest with `apply-release --rollback`. Once new writes are accepted on PostgreSQL 17, there is no safe binary downgrade; rollback requires a planned reverse logical restore and data-loss decision. Never delete the 15 volume as part of the upgrade command.
