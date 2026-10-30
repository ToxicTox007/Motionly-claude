#!/bin/sh
# Entrypoint of supabase-storage: connect to the database with the
# supabase_storage_admin password from this service's own credentials volume
# (written by infra/db/apply-role-passwords.sh) and verify the server's TLS
# certificate against the internal database CA (database audit DB-21).
# The password is read here, so it never appears in the Compose environment.
set -eu
password="$(cat /var/lib/motionly-db-credentials/password)"
export DATABASE_URL="postgres://supabase_storage_admin:${password}@postgres:5432/postgres?sslmode=verify-full&sslrootcert=/etc/motionly-db-tls/ca/ca.crt"
unset password
exec docker-entrypoint.sh node dist/start/server.js
