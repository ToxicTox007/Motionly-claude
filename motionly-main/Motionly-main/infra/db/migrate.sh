#!/bin/sh
# Apply pending migrations on every release (the one-shot `db-migrate` service).
#
# Runs after Postgres is healthy and after Auth and Storage have created their
# own schemas. Each applied version is recorded in
# supabase_migrations.schema_migrations, the table the Supabase CLI uses, so
# `supabase migration list --db-url ...` reports the same history.
#
# Everything runs in one psql session that holds an advisory lock, so two
# releases never migrate concurrently. A migration without its own transaction
# is wrapped in one together with its history row, so it is applied and
# recorded atomically (the CLI also applies such a file as one implicit
# transaction). A migration that manages its own transaction (begin; ...
# commit;) runs as written and is recorded immediately after it commits.
# Like the CLI, a pending migration older than the latest applied one is
# rejected instead of being applied out of order.
set -eu

migrations_dir="${MIGRATIONS_DIR:-/opt/motionly-db/migrations}"
PGPASSWORD="$(cat /run/secrets/postgres_password)"
export PGPASSWORD
export PGHOST="${PGHOST:-postgres}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGDATABASE="${PGDATABASE:-postgres}"

files="$(find "$migrations_dir" -maxdepth 1 -type f -name '*.sql' | LC_ALL=C sort)"
if [ -z "$files" ]; then
  echo "db-migrate: no migrations found in $migrations_dir" >&2
  exit 1
fi

plan="$(mktemp)"
trap 'rm -f "$plan"' EXIT

{
  cat <<'SQL'
set client_min_messages = warning;
select pg_advisory_lock(hashtext('motionly.db-migrate')) as locked \gset
create schema if not exists supabase_migrations;
create table if not exists supabase_migrations.schema_migrations (
  version text not null primary key
);
alter table supabase_migrations.schema_migrations add column if not exists statements text[];
alter table supabase_migrations.schema_migrations add column if not exists name text;
select coalesce(max(version), '') as latest_applied
  from supabase_migrations.schema_migrations \gset
select 0 as applied_count \gset
SQL
  for path in $files; do
    file="$(basename "$path")"
    if ! printf '%s\n' "$file" | grep -Eq '^[0-9]{12}_[a-z0-9_]+\.sql$'; then
      echo "db-migrate: invalid migration file name: $file" >&2
      exit 1
    fi
    version="${file%%_*}"
    name="${file#*_}"
    name="${name%.sql}"
    record="insert into supabase_migrations.schema_migrations (version, name) values ('$version', '$name');"
    cat <<SQL
select exists (
  select 1 from supabase_migrations.schema_migrations where version = '$version'
) as already_applied \\gset
\\if :already_applied
\\else
select '$version' < :'latest_applied' as out_of_order \\gset
\\if :out_of_order
\\echo 'db-migrate: $file is older than the latest applied migration' :latest_applied
do \$\$ begin raise exception 'db-migrate: refusing to apply an out-of-order migration'; end \$\$;
\\endif
\\echo 'db-migrate: applying $file'
SQL
    if grep -qiE '^[[:space:]]*begin[[:space:]]*;' "$path"; then
      printf '\\i %s\n%s\n' "$path" "$record"
    else
      printf 'begin;\n\\i %s\n%s\ncommit;\n' "$path" "$record"
    fi
    cat <<'SQL'
select :applied_count + 1 as applied_count \gset
\endif
SQL
  done
  cat <<'SQL'
notify pgrst, 'reload schema';
\echo 'db-migrate: applied' :applied_count 'migration(s)'
select pg_advisory_unlock(hashtext('motionly.db-migrate')) as unlocked \gset
SQL
} > "$plan"

psql -X --no-psqlrc -q -v ON_ERROR_STOP=1 -f "$plan"
