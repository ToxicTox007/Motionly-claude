#!/bin/sh
set -eu

postgres_password="$(cat /run/secrets/postgres_password)"
temporal_password="$(cat /run/secrets/temporal_db_password)"
export PGPASSWORD="$postgres_password"

# psql interpolates :'variables' only in SQL read from stdin or a file (not in
# SQL passed as a command-line argument), so every statement is sent on stdin.
psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --set ON_ERROR_STOP=1 \
  --set temporal_password="$temporal_password" <<'SQL'
select format('create role temporal login password %L', :'temporal_password')
where not exists (select 1 from pg_roles where rolname = 'temporal')
\gexec
-- PostgreSQL 16+ lets a non-superuser create a database owned by another role
-- only when it can SET ROLE to it.
grant temporal to postgres;
SQL

for database in temporal temporal_visibility; do
  # CREATE DATABASE cannot run in a transaction block; \gexec runs each
  # generated statement on its own.
  psql -h temporal-postgres -U postgres -d postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --set database_name="$database" <<'SQL'
select format('create database %I owner temporal', :'database_name')
where not exists (select 1 from pg_database where datname = :'database_name')
\gexec
select format('grant all privileges on database %I to temporal', :'database_name')
\gexec
SQL
done
