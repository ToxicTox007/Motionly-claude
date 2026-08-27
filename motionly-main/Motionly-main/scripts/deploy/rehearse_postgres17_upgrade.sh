# Rehearse a PostgreSQL 15 -> 17 logical migration through protected libpq services.
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 <postgres-15-source-service> <empty-postgres-17-target-service>" >&2
  exit 64
fi
if [[ "${MOTIONLY_POSTGRES17_UPGRADE_ACK:-}" != "pg15-to-pg17" ]]; then
  echo "Set MOTIONLY_POSTGRES17_UPGRADE_ACK=pg15-to-pg17 to confirm the empty PostgreSQL 17 target may be restored." >&2
  exit 64
fi
: "${PGSERVICEFILE:?PGSERVICEFILE must point to protected pg_service.conf}"
: "${PGPASSFILE:?PGPASSFILE must point to protected pgpass}"

source_service="$1"
target_service="$2"
for service in "$source_service" "$target_service"; do
  [[ "$service" =~ ^[A-Za-z0-9_.-]+$ ]] || {
    echo "PostgreSQL service name is invalid." >&2
    exit 64
  }
done
for command in psql pg_dump pg_restore; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required for PostgreSQL 17 rehearsal" >&2
    exit 127
  }
done

source_major="$(psql "service=$source_service" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'show server_version_num;' | tr -d '[:space:]')"
target_major="$(psql "service=$target_service" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'show server_version_num;' | tr -d '[:space:]')"
[[ "$source_major" =~ ^15[0-9]{4}$ ]] || {
  echo "source must be PostgreSQL 15, got server_version_num=$source_major" >&2
  exit 65
}
[[ "$target_major" =~ ^17[0-9]{4}$ ]] || {
  echo "target must be PostgreSQL 17, got server_version_num=$target_major" >&2
  exit 65
}

# verify_backup_restore refuses a non-empty target, confirms fingerprints after
# logical pg_dump/pg_restore, and reports a writable restored database.
MOTIONLY_BACKUP_RESTORE_ACK=restore-target \
  bash "$(dirname "$0")/../verify_backup_restore.sh" "$source_service" "$target_service"

echo "PostgreSQL 15 -> 17 logical restore rehearsal passed. Keep the PostgreSQL 15 volume untouched until release acceptance is complete."
