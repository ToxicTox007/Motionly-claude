# Verify a logical backup/restore using named libpq services, never database URLs.
set -euo pipefail
umask 077

for command in pg_dump pg_restore psql stat; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command is required for backup/restore verification" >&2
    exit 127
  fi
done

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 <source-pg-service> <empty-restore-pg-service>" >&2
  exit 64
fi
if [[ "${MOTIONLY_BACKUP_RESTORE_ACK:-}" != "restore-target" ]]; then
  echo "Set MOTIONLY_BACKUP_RESTORE_ACK=restore-target to confirm the restore service may be cleared." >&2
  exit 64
fi
: "${PGSERVICEFILE:?PGSERVICEFILE must point to a protected pg_service.conf}"
: "${PGPASSFILE:?PGPASSFILE must point to a protected pgpass file}"
[[ -r "$PGSERVICEFILE" && -r "$PGPASSFILE" ]] || {
  echo "PGSERVICEFILE and PGPASSFILE must be readable protected files" >&2
  exit 64
}
for protected_file in "$PGSERVICEFILE" "$PGPASSFILE"; do
  mode="$(stat -c '%a' "$protected_file")"
  (( (8#$mode & 8#077) == 0 )) || {
    echo "credential file permissions must not grant group/world access: $protected_file" >&2
    exit 64
  }
done

source_service="$1"
restore_service="$2"
for service in "$source_service" "$restore_service"; do
  [[ "$service" =~ ^[A-Za-z0-9_.-]+$ ]] || {
    echo "PostgreSQL service names may contain only letters, digits, dot, underscore, and hyphen." >&2
    exit 64
  }
done

connection() {
  printf 'service=%s' "$1"
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/motionly-backup-restore.XXXXXX")"
chmod 700 "$work_dir"
dump_file="$work_dir/motionly-prelaunch.dump"
source_fingerprint="$work_dir/source-fingerprint.tsv"
restore_fingerprint="$work_dir/restore-fingerprint.tsv"
report_file="$work_dir/report.json"

cleanup() {
  rm -f "$dump_file" "$source_fingerprint" "$restore_fingerprint" "$report_file"
  rmdir "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT

database_identity() {
  psql "$(connection "$1")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "select coalesce(inet_server_addr()::text, 'local') || ':' || coalesce(inet_server_port()::text, '0') || '/' || current_database();"
}

fingerprint_tables() {
  psql "$(connection "$1")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 <<'SQL' | LC_ALL=C sort
select format(
  'select %L || chr(9) || count(*)::text || chr(9) || coalesce(bit_xor(hashtextextended(to_jsonb(t)::text, 0)), 0)::text from %I.%I t;',
  table_schema || '.' || table_name,
  table_schema,
  table_name
)
from information_schema.tables
where table_schema = 'public'
  and table_type = 'BASE TABLE'
order by table_name;
\gexec
SQL
}

source_identity="$(database_identity "$source_service")"
restore_identity="$(database_identity "$restore_service")"
if [[ "$source_identity" == "$restore_identity" ]]; then
  echo "Restore target resolves to the source database; refusing destructive restore." >&2
  exit 64
fi

fingerprint_tables "$source_service" > "$source_fingerprint"
test -s "$source_fingerprint"
source_table_count="$(wc -l < "$source_fingerprint" | tr -d '[:space:]')"
restore_table_count="$(psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command "select count(*) from information_schema.tables where table_schema = 'public';" | tr -d '[:space:]')"
if [[ "$restore_table_count" != "0" ]]; then
  echo "Restore target must be empty; refusing to overwrite existing public tables." >&2
  exit 64
fi

pg_dump --format=custom --no-owner --no-acl --exclude-table-data=vault.secrets --file "$dump_file" "$(connection "$source_service")"
pg_restore --exit-on-error --no-owner --dbname "$(connection "$restore_service")" "$dump_file"

psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --set ON_ERROR_STOP=1 \
  --command "select count(*) from information_schema.tables where table_schema = 'public';" \
  | grep -Eq '[1-9][0-9]*'
psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command "select not pg_is_in_recovery();" | grep -qx 't'
fingerprint_tables "$restore_service" > "$restore_fingerprint"
cmp -s "$source_fingerprint" "$restore_fingerprint"

printf '{"verified":true,"public_table_count":%s,"data_fingerprints_match":true,"target_writable":true}\n' \
  "$source_table_count" > "$report_file"
if [[ -n "${MOTIONLY_BACKUP_RESTORE_REPORT_PATH:-}" ]]; then
  install -D -m 600 "$report_file" "$MOTIONLY_BACKUP_RESTORE_REPORT_PATH"
fi
printf 'Backup/restore verification succeeded for %s public tables.\n' "$source_table_count"
