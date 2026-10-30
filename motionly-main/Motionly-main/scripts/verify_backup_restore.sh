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
source_privileges="$work_dir/source-privileges.tsv"
restore_privileges="$work_dir/restore-privileges.tsv"
restore_list="$work_dir/restore.list"
report_file="$work_dir/report.json"

cleanup() {
  rm -f "$dump_file" "$source_fingerprint" "$restore_fingerprint" "$source_privileges" "$restore_privileges" "$restore_list" "$report_file"
  rmdir "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT

database_identity() {
  psql "$(connection "$1")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "select coalesce(inet_server_addr()::text, 'local') || ':' || coalesce(inet_server_port()::text, '0') || '/' || current_database();"
}

fingerprint_tables() {
  # The generator query must not end in a semicolon: psql would run it once on
  # its own (printing the generated statements) and again for \gexec.
  psql "$(connection "$1")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 <<'SQL' | grep -v '^$' | LC_ALL=C sort
select format(
  'select %L || chr(9) || count(*)::text || chr(9) || coalesce(bit_xor(hashtextextended(to_jsonb(t)::text, 0)), 0)::text from %I.%I t;',
  table_schema || '.' || table_name,
  table_schema,
  table_name
)
from information_schema.tables
where table_schema = 'public'
  and table_type = 'BASE TABLE'
order by table_name
\gexec
SQL
}

# What each API role may do with every table and function in public, plus the
# row-level security state and policies (database audit DB-03). A restore that
# drops grants either breaks the app (service_role loses its tables) or, if
# grants are re-added by hand, can leave SECURITY DEFINER functions callable by
# anon. The restored database must match the source exactly.
fingerprint_privileges() {
  psql "$(connection "$1")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 <<'SQL' | grep -v '^$' | LC_ALL=C sort
with roles(name) as (
  select rolname from pg_roles
  where rolname in ('anon', 'authenticated', 'service_role', 'motionly_worker')
)
select 'table' || chr(9) || c.relname || chr(9) || r.name || chr(9) ||
       concat_ws(',',
         case when has_table_privilege(r.name, c.oid, 'SELECT') then 'S' end,
         case when has_table_privilege(r.name, c.oid, 'INSERT') then 'I' end,
         case when has_table_privilege(r.name, c.oid, 'UPDATE') then 'U' end,
         case when has_table_privilege(r.name, c.oid, 'DELETE') then 'D' end,
         case when has_table_privilege(r.name, c.oid, 'TRUNCATE') then 'T' end)
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
cross join roles r
where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
union all
select 'function' || chr(9) || p.oid::regprocedure::text || chr(9) || r.name || chr(9) ||
       has_function_privilege(r.name, p.oid, 'EXECUTE')::text || chr(9) || p.prosecdef::text
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
cross join roles r
where n.nspname = 'public'
union all
select 'rls' || chr(9) || c.relname || chr(9) || c.relrowsecurity::text || chr(9) || c.relforcerowsecurity::text
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r', 'p')
union all
select 'policy' || chr(9) || tablename || chr(9) || policyname || chr(9) || cmd || chr(9) ||
       array_to_string(roles, ',') || chr(9) || coalesce(qual, '') || chr(9) || coalesce(with_check, '')
from pg_policies
where schemaname = 'public';
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
fingerprint_privileges "$source_service" > "$source_privileges"
test -s "$source_privileges"
source_table_count="$(wc -l < "$source_fingerprint" | tr -d '[:space:]')"
restore_table_count="$(psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command "select count(*) from information_schema.tables where table_schema = 'public';" | tr -d '[:space:]')"
if [[ "$restore_table_count" != "0" ]]; then
  echo "Restore target must be empty; refusing to overwrite existing public tables." >&2
  exit 64
fi

# Grants and policies travel with the dump (no --no-acl): a restore that loses
# them is not a usable restore. Ownership is reassigned to the restoring role
# (--no-owner), which owns every public object in the source as well. Grants on
# the schemas Supabase manages (extensions, auth, storage, vault, realtime,
# graphql, pgbouncer) belong to its superuser and are recreated by the image,
# so only the application schemas' grants are replayed.
pg_dump --format=custom --no-owner --exclude-table-data=vault.secrets --file "$dump_file" "$(connection "$source_service")"
restore_role="$(psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --no-align \
  --set ON_ERROR_STOP=1 --command "select current_user;")"
pg_restore --list "$dump_file" | awk -v role="$restore_role" '
  $4 == "ACL" {
    if ($5 == "-") { keep = ($6 == "SCHEMA" && ($7 == "public" || $7 == "mock_gateway")) }
    else { keep = ($5 == "public" || $5 == "mock_gateway") }
    if (keep) print
    next
  }
  $4 == "DEFAULT" && $5 == "ACL" {
    if (($6 == "public" || $6 == "mock_gateway") && $NF == role) print
    next
  }
  { print }
' > "$restore_list"
pg_restore --exit-on-error --no-owner --use-list "$restore_list" --dbname "$(connection "$restore_service")" "$dump_file"

psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --set ON_ERROR_STOP=1 \
  --command "select count(*) from information_schema.tables where table_schema = 'public';" \
  | grep -Eq '[1-9][0-9]*'
psql "$(connection "$restore_service")" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command "select not pg_is_in_recovery();" | grep -qx 't'
fingerprint_tables "$restore_service" > "$restore_fingerprint"
cmp -s "$source_fingerprint" "$restore_fingerprint"
fingerprint_privileges "$restore_service" > "$restore_privileges"
if ! cmp -s "$source_privileges" "$restore_privileges"; then
  echo "Restored privileges, row-level security or policies differ from the source:" >&2
  diff "$source_privileges" "$restore_privileges" | head -n 40 >&2 || true
  exit 1
fi

printf '{"verified":true,"public_table_count":%s,"data_fingerprints_match":true,"privileges_match":true,"target_writable":true}\n' \
  "$source_table_count" > "$report_file"
if [[ -n "${MOTIONLY_BACKUP_RESTORE_REPORT_PATH:-}" ]]; then
  install -D -m 600 "$report_file" "$MOTIONLY_BACKUP_RESTORE_REPORT_PATH"
fi
printf 'Backup/restore verification succeeded for %s public tables.\n' "$source_table_count"
