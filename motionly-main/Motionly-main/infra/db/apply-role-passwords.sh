#!/bin/sh
# Give each Supabase service role its own database password (database audit
# DB-21). Until now Auth, REST and Storage logged in with the postgres
# superuser's password (infra/db/roles.sql set all three to it), so any one of
# them held the credential to the whole database.
#
# Run by the one-shot db-roles service on every release, after postgres is
# healthy and before Auth, REST and Storage start. db-roles shares the
# postgres container's network namespace (network_mode: service:postgres) and
# connects over its loopback, which pg_hba.conf trusts, as supabase_admin.
#
# Each service has its own credentials volume, mounted only into that service
# and db-roles:
#   db-auth-credentials     password  (supabase_auth_admin; infra/db/start-auth.sh)
#   db-rest-credentials     db-uri    (authenticator; PostgREST reads it with @file)
#   db-storage-credentials  password  (supabase_storage_admin; infra/db/start-storage.sh)
# A password is generated the first time and kept; every run sets it on the
# role again, so a restored or rebuilt database always matches the services.
# Delete a volume to rotate that service's password on the next release.
# Realtime still logs in as supabase_admin with postgres_password: it needs
# that superuser, and re-seeds its tenant connection from it on every start.
set -eu
umask 077

credentials="${MOTIONLY_DB_CREDENTIALS_DIR:-/var/lib/motionly-db-credentials}"
ca_cert="${MOTIONLY_DB_TLS_CA_CERT:-/etc/motionly-db-tls/ca/ca.crt}"
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-supabase_admin}"
export PGDATABASE="${PGDATABASE:-postgres}"

new_password() {
  # 64 hex characters: safe in a connection URL without escaping.
  openssl rand -hex 32
}

password_file() {
  # The service's password, created once.
  file="$credentials/$1/password"
  if [ ! -s "$file" ]; then
    new_password >"$file.new"
    mv "$file.new" "$file"
    echo "db-roles: generated a password for the $1 service" >&2
  fi
  chmod 0444 "$file"
  printf '%s' "$file"
}

auth_password="$(cat "$(password_file auth)")"
rest_password="$(cat "$(password_file rest)")"
storage_password="$(cat "$(password_file storage)")"
for value in "$auth_password" "$rest_password" "$storage_password"; do
  printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' || {
    echo "db-roles: a stored password is not 64 hex characters" >&2
    exit 1
  }
done

# PostgREST cannot run a shell, so it reads its whole connection string from a
# file (PGRST_DB_URI=@/var/lib/motionly-db-credentials/db-uri).
printf 'postgres://authenticator:%s@postgres:5432/postgres?sslmode=verify-full&sslrootcert=%s\n' \
  "$rest_password" "$ca_cert" >"$credentials/rest/db-uri.new"
mv "$credentials/rest/db-uri.new" "$credentials/rest/db-uri"
chmod 0444 "$credentials/rest/db-uri"

# Hex values need no quoting; they reach psql on stdin, never in argv.
{
  printf '\\set auth_password %s\n' "$auth_password"
  printf '\\set rest_password %s\n' "$rest_password"
  printf '\\set storage_password %s\n' "$storage_password"
  cat <<'SQL'
-- These statements carry passwords: keep them out of the server log.
set log_statement = 'none';
set log_min_duration_statement = -1;
alter role supabase_auth_admin with password :'auth_password';
alter role authenticator with password :'rest_password';
alter role supabase_storage_admin with password :'storage_password';
\echo 'db-roles: service role passwords applied'
SQL
} | psql -X --no-psqlrc -q -v ON_ERROR_STOP=1
