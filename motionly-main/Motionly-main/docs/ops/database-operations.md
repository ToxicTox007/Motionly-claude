# Database operations

How the production PostgreSQL databases (`postgres` for Supabase, `temporal-postgres` for Temporal) are backed up, restored, monitored and migrated on the Compose host. Both run `supabase/postgres:17.4.1.069` from `infra/docker-compose.gpu.yml`.

## Backups

Three independent layers:

| Layer                  | What                                                                                                                    | Where                                                        | Kept                                               |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | -------------------------------------------------- |
| WAL archive            | Every WAL segment, pushed by `archive_command=wal-g wal-push` (at least every `archive_timeout=300` s)                  | `postgres-backups` / `temporal-postgres-backups` volumes     | Back to the oldest local base backup               |
| Base backups + S3 copy | `db-backup daily` (02:30 UTC) takes a WAL-G base backup; `db-backup frequent` copies both volumes to S3 every 5 minutes | `s3://<BackupsBucketName>/database/<environment>/<service>/` | 35 days (bucket lifecycle); 2 base backups locally |
| Data volume snapshots  | Data Lifecycle Manager snapshots the tagged data volume daily at 03:30 UTC (crash-consistent)                           | EBS snapshots                                                | 14                                                 |

Point-in-time recovery is possible to any moment in the last 30 days. Worst-case data loss after losing the whole instance is about 10 minutes (one `archive_timeout` plus one sync interval); after losing only the database container or its volume, it is the WAL not yet archived (at most 5 minutes).

The agent is `scripts/deploy/db_backup.sh`, installed as `/opt/motionly/agent/bin/db-backup` by `scripts/deploy/bootstrap_compose_host.sh` together with the `motionly-db-backup-daily` and `motionly-db-backup-frequent` systemd timers. It runs on the host as root and uses the instance role; containers never get AWS credentials (IMDS hop limit 1). It reads `/etc/motionly/host.env`:

- `MOTIONLY_BACKUP_BUCKET` (required): the stack's `BackupsBucketName` output.
- `MOTIONLY_BACKUP_LOCAL_FULL_COPIES` (default `2`): base backups kept on the host.

Enabling it on an existing host: set `MOTIONLY_BACKUP_BUCKET`, rerun `bootstrap_compose_host.sh`, then check `systemctl list-timers 'motionly-db-backup*'` and run `systemctl start motionly-db-backup@daily.service` once so the first base backup exists before the alarms evaluate.

## Alarms

`db-backup` publishes metrics to the CloudWatch namespace `Motionly/Database` (dimensions `Environment`, `Service`). The stack (`scripts/aws/render_template.py`) alarms on them and notifies the `DatabaseAlarmTopic` SNS topic; set the `AlarmEmail` stack parameter to subscribe an address (confirm the subscription email).

| Alarm                                                              | Fires when                                                |
| ------------------------------------------------------------------ | --------------------------------------------------------- |
| `PostgresDownAlarm`, `TemporalPostgresDownAlarm`                   | the database has not answered `pg_isready` for 10 minutes |
| `PostgresBaseBackupAgeAlarm`, `TemporalPostgresBaseBackupAgeAlarm` | no base backup in the last 26 hours                       |
| `PostgresBackupSyncAgeAlarm`, `TemporalPostgresBackupSyncAgeAlarm` | the backup volume has not reached S3 for 30 minutes       |
| `PostgresWalArchiveAlarm`, `TemporalPostgresWalArchiveAlarm`       | WAL archiving has been failing for 15 minutes             |
| `DataVolumeUsageAlarm`                                             | `/srv/motionly` is more than 80% full                     |

Every alarm except the WAL one treats missing data as breaching, so a stopped agent also alarms. A failing WAL archive is urgent: PostgreSQL keeps every unarchived segment, so the disk fills until archiving recovers. Check `docker compose logs postgres` for the `wal-g wal-push` error and the backup volume's free space.

## Point-in-time recovery

Restore into a new volume and a side container first, check the data, and only then cut over. Steps 1–3 were run end to end against the production image; replace `<...>` placeholders. Run them on the host from the active release directory.

1. Choose the target time (UTC) just before the incident, and the newest base backup that finished before it (`LATEST` is right only when the latest backup is older than the target; otherwise replay stops with "recovery ended before configured recovery target was reached"):

   ```sh
   pg=$(docker compose ps -q postgres)
   docker exec -u postgres "$pg" wal-g backup-list --detail
   ```

   If the host or its backup volume is lost, copy the backups from S3 into a directory first and mount that instead of the volume in step 2:

   ```sh
   aws s3 sync "s3://<BackupsBucketName>/database/<environment>/postgres/" /srv/motionly/restore/walg/
   ```

2. Fetch the base backup into an empty volume. `volume-nocopy` matters: without it Docker copies the image's data directory into the new volume and WAL-G refuses with "Directory ... for delta base must be empty".

   ```sh
   image=$(docker inspect -f '{{.Config.Image}}' "$pg")
   docker volume create motionly-pitr-data
   docker run --rm -u root \
     --mount type=volume,src=motionly-pitr-data,dst=/var/lib/postgresql/data,volume-nocopy=true \
     --entrypoint sh "$image" -c 'chown 101:102 /var/lib/postgresql/data && chmod 700 /var/lib/postgresql/data'
   docker run --rm -u postgres \
     --mount type=volume,src=motionly-pitr-data,dst=/var/lib/postgresql/data,volume-nocopy=true \
     -v motionly_postgres-backups:/var/lib/motionly-backups/postgres:ro \
     -e WALG_FILE_PREFIX=/var/lib/motionly-backups/postgres \
     --entrypoint sh "$image" -c \
     'wal-g backup-fetch /var/lib/postgresql/data <backup-name> && touch /var/lib/postgresql/data/recovery.signal'
   ```

3. Replay WAL to the target time in a side container (no network, archiving off so it never writes into the production archive):

   ```sh
   docker run -d --name motionly-pitr --network none \
     --mount type=volume,src=motionly-pitr-data,dst=/var/lib/postgresql/data \
     -v motionly_postgres-backups:/var/lib/motionly-backups/postgres:ro \
     -e WALG_FILE_PREFIX=/var/lib/motionly-backups/postgres \
     "$image" postgres -D /etc/postgresql \
     -c "restore_command=wal-g wal-fetch %f %p" \
     -c "recovery_target_time=<2026-10-28 10:33:38+00>" \
     -c recovery_target_action=promote -c archive_mode=off
   docker logs -f motionly-pitr     # wait for "database system is ready to accept connections"
   docker exec motionly-pitr psql -U supabase_admin -h 127.0.0.1 -d postgres -c 'select pg_is_in_recovery(), now();'
   ```

   `pg_is_in_recovery()` is `f` once the target is reached and the server promoted. Inspect the data (`docker exec -it motionly-pitr psql ...`).

4. Cut over (not yet rehearsed end to end; rehearse on staging first). Stop the stack, keep the damaged volume, and copy the recovered data into the Compose volume:

   ```sh
   docker stop motionly-pitr
   docker compose stop
   docker volume create motionly_supabase-db-before-restore
   docker run --rm -v motionly_supabase-db:/from:ro -v motionly_supabase-db-before-restore:/to \
     --entrypoint sh "$image" -c 'cp -a /from/. /to/'
   docker run --rm -v motionly_supabase-db:/to -v motionly-pitr-data:/from:ro \
     --entrypoint sh "$image" -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
   docker compose up -d
   systemctl start motionly-db-backup@daily.service   # new timeline: take a fresh base backup
   ```

The same procedure restores `temporal-postgres` with `temporal-postgres-backups`, `/var/lib/motionly-backups/temporal` and `motionly_temporal-db`. Restoring from an EBS snapshot instead: create a volume from the snapshot, attach and mount it, and start the stack on it; PostgreSQL performs crash recovery.

## Restore drill

`scripts/verify_backup_restore.sh <source-service> <empty-restore-service>` dumps the source with `pg_dump --format=custom`, restores it into an empty database and fails unless every public table's row count and content hash, and every API role's table and function privileges, row-level security flags and policies, match the source. It takes libpq service names, never URLs: put the connection details in a `pg_service.conf` and the passwords in a `pgpass` file, both mode `0600`, and export `PGSERVICEFILE` and `PGPASSFILE` ([PostgreSQL 17 upgrade](postgresql-17-upgrade.md) has an example). Grants on the application schemas (`public`, `mock_gateway`) are restored from the dump; the Supabase-managed schemas get theirs from the image. CI (`phase-0-safety.yml`) runs the drill against local Supabase on every change.

## Migrations

`infra/db/migrate.sh` (the `db-migrate` service) applies pending migrations in order on every release:

- Each DDL statement waits at most `MIGRATION_LOCK_TIMEOUT` (default `15s`) for a lock. On timeout the migration rolls back, is not recorded, and the release fails; rerun it when the long transaction holding the lock has finished. A waiting `ALTER TABLE` would otherwise block every later query on the table.
- `scripts/check_forward_migrations.sh` (CI) rejects edits to applied migrations, new migrations that do not sort after the newest one on the base branch, and file names `migrate.sh` would refuse (`<12-digit version>_<lower_snake_name>.sql`).
- New tables, sequences and functions in `public` are no longer granted to `anon` or `authenticated` automatically (migration `202610280201`). A migration that adds something the browser reads directly must grant it explicitly; a new `SECURITY DEFINER` function the browser calls must also be added to the allowlist in `supabase/tests/db_audit_api_role_privileges_test.sql`.

### Large databases: pre-build the foreign-key indexes

Migration `202610280203` creates 113 foreign-key indexes inside the release transaction, which blocks writes to each table while its index builds. That takes seconds on a small database. When tables hold millions of rows, build the indexes online before the release; the migration then finds them and does nothing:

```sh
psql "service=motionly_production" -v ON_ERROR_STOP=1 -f scripts/db/prebuild_foreign_key_indexes.sql
```

An interrupted build leaves an `INVALID` index; the file's header shows how to find and drop it before rerunning.

### Deferred constraints

Migration `202610280206` validates the constraints that earlier migrations added `NOT VALID`. A constraint that existing rows violate stays `NOT VALID` (new rows are still checked) and the migration logs a `WARNING` instead of failing the release. List what is left:

```sql
select conrelid::regclass, conname
from pg_constraint
where connamespace = 'public'::regnamespace and not convalidated;
```

For the tenant-consistency (`task3_*`) constraints, `select public.motionly_audit_task3_tenant_integrity();` (as `service_role`) records each offending row in `tenant_integrity_findings`. After repairing them, `select * from public.motionly_validate_task3_tenant_integrity('validate-task3-tenant-integrity');` validates the set; for any other constraint run `alter table <table> validate constraint <name>;`.

## Account deletion and workspace ownership

Deleting an account (`auth.users`, for example through the GoTrue admin API) no longer deletes workspaces other people use. For each workspace the account owns:

- another active owner exists: ownership passes to the longest-standing one;
- other active members but no other owner: the deletion is refused with `ACCOUNT_OWNS_SHARED_WORKSPACE`; transfer ownership (`motionly_transfer_workspace_ownership`) or remove the members first;
- nobody else is active: the workspace and its projects are deleted, as before.

## Connections: TLS and per-service passwords

Both database servers serve TLS and accept a password login from another container only over TLS; every client verifies the server certificate and host name (`sslmode=verify-full`, Realtime `DB_SSL`, Temporal `sql.tls`). The one-shot `db-tls` service issues the internal CA and the server certificate on first start and re-issues the certificate before it expires; `db-roles` gives Auth (`supabase_auth_admin`), REST (`authenticator`) and Storage (`supabase_storage_admin`) their own passwords on every release, so none of them holds the `postgres` password. Details, rotation and the rollback behaviour are in [Runtime secrets](../security/secrets.md#database-credentials-and-tls).

Two things to know when operating the databases by hand:

- Inside a database container (`docker compose exec postgres psql -h 127.0.0.1 ...`) the loopback is trusted, exactly as before; from any other container, connect with `sslmode=verify-full` and the CA in the `postgres-tls-ca` volume (`/etc/motionly-db-tls/ca/ca.crt`).
- Supabase Realtime's tenant connection cannot use TLS (its self-hosted seed pins `ssl_enforced=false`), so `supabase_admin` alone may still log in to the Supabase database without TLS (`infra/db/pg_hba.conf`).

## Production image in CI

The Supabase CLI pinned in CI (2.104.0) runs PostgreSQL `17.6.1.107` for `supabase start` / `supabase test db`, while production runs `17.4.1.069`. The `db` verify stage therefore also runs `scripts/db/test_production_image.sh`: it starts the production postgres, Auth, REST, Storage and Realtime images with the production TLS and password setup, applies every migration through `infra/db/migrate.sh` and runs every pgTAP test. Run it locally with `bash scripts/db/test_production_image.sh [test.sql ...]` (Docker only; `MOTIONLY_DB_TEST_KEEP=1` keeps the containers).

## Render attempts

A render belongs to one worker run at a time (`render_jobs.attempt_id`, migration `202610280209`). The worker generates an attempt id per run and claims the render with `motionly_claim_render_attempt`; every progress, failure and completion write of that run is conditional on still owning it. A stalled BullMQ redelivery takes the render over, and requeueing a render releases its attempt, so a stale run's late writes match nothing and it stops (`render_attempt_superseded`, never retried).

## Payment gateway webhooks

Subscription changes from gateway webhooks are applied in the order the gateway created the events (`subscriptions.last_gateway_event_at`, migration `202610280208`): a late, older event (a renewal charge delivered after a cancellation) no longer changes the subscription, although its charge is still recorded. The billing period of a payment is the calendar month the payment was made in, not the month the webhook arrived in.
