#!/bin/sh
set -eu

postgres_password="$(cat /run/secrets/postgres_password)"
temporal_password="$(cat /run/secrets/temporal_db_password)"
export PGPASSWORD="$postgres_password"

psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --set ON_ERROR_STOP=1 \
  --set temporal_password="$temporal_password" <<'SQL'
select format('create role temporal login password %L', :'temporal_password')
where not exists (select 1 from pg_roles where rolname = 'temporal')
\gexec
SQL

for database in temporal temporal_visibility; do
  exists="$(psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --set database_name="$database" \
    --command 'select 1 from pg_database where datname = :'"'"'database_name'"'"';')"
  if [ "$exists" != "1" ]; then
    psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --set ON_ERROR_STOP=1 \
      --set database_name="$database" \
      --command 'create database :"database_name" owner temporal'
  fi
  psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --set database_name="$database" \
    --command 'grant all privileges on database :"database_name" to temporal'
done
