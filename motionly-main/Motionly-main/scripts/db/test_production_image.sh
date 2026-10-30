#!/usr/bin/env bash
# Apply every migration and run every pgTAP test against the images and the
# database connection setup production runs (database audit DB-21, DB-24).
#
# `supabase start` / `supabase test db` use the Postgres image bundled with the
# pinned Supabase CLI, which is not the one infra/docker-compose.gpu.yml pins.
# This script takes the postgres, Auth, REST, Storage and Realtime images from
# the production Compose file and starts them the way Compose does:
#
#   * db-tls (infra/db/generate-tls.sh) issues the internal CA and the server
#     certificate; postgres serves TLS and uses infra/db/pg_hba.conf, which
#     refuses a password login without TLS from outside the container;
#   * db-roles (infra/db/apply-role-passwords.sh) gives Auth, REST and Storage
#     their own passwords, which they read from their own credentials volumes
#     (infra/db/start-auth.sh, start-storage.sh, PostgREST's @file db-uri);
#   * every client verifies the server certificate (sslmode=verify-full);
#   * migrations run through infra/db/migrate.sh like the db-migrate service.
#
# Clients reach the server through the database container's own network
# address (named `postgres`, as on the host), not its loopback, so the
# password and TLS rules are exercised. All containers share that container's
# network namespace, so this needs nothing but a Docker daemon. Everything is
# throwaway: containers and volumes are removed on exit unless
# MOTIONLY_DB_TEST_KEEP=1.
#
# Usage: scripts/db/test_production_image.sh [test.sql ...]
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose="$root/infra/docker-compose.gpu.yml"
prefix="${MOTIONLY_DB_TEST_PREFIX:-motionly-dbtest}"
db="$prefix-db"
tls_ca_key="$prefix-tls-ca-key"
tls_ca="$prefix-tls-ca"
tls_server="$prefix-tls-server"
auth_credentials="$prefix-auth-credentials"
rest_credentials="$prefix-rest-credentials"
storage_credentials="$prefix-storage-credentials"
volumes=("$tls_ca_key" "$tls_ca" "$tls_server" "$auth_credentials" "$rest_credentials" "$storage_credentials")

for command in docker python3; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 127
  }
done

# The image a production service runs: the first `image:` under its header.
image_of() {
  awk -v header="  $1:" '
    $0 == header { found = 1; next }
    found && /^    image:/ { print $2; exit }
    found && /^  [a-z]/ { exit }
  ' "$compose"
}
postgres_image="$(image_of postgres)"
auth_image="$(image_of supabase-auth)"
rest_image="$(image_of supabase-rest)"
storage_image="$(image_of supabase-storage)"
realtime_image="$(image_of supabase-realtime)"
for image in "$postgres_image" "$auth_image" "$rest_image" "$storage_image" "$realtime_image"; do
  [[ "$image" == *@sha256:* ]] || {
    echo "expected a digest-pinned image in $compose, got '$image'" >&2
    exit 1
  }
done

work="$(mktemp -d "${TMPDIR:-/tmp}/motionly-dbtest.XXXXXX")"
containers=("$prefix-realtime" "$prefix-storage" "$prefix-rest" "$prefix-auth" "$db")
cleanup() {
  status=$?
  if [[ "${MOTIONLY_DB_TEST_KEEP:-0}" != "1" ]]; then
    docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
    docker volume rm "${volumes[@]}" >/dev/null 2>&1 || true
    rm -rf "$work"
  else
    # The containers bind-mount files from $work, so it stays as well.
    echo "Kept containers: ${containers[*]} (database on 127.0.0.1:5432 inside $db); files in $work"
  fi
  exit "$status"
}
trap cleanup EXIT
docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
docker volume rm "${volumes[@]}" >/dev/null 2>&1 || true

random_hex() { python3 -c 'import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }
postgres_password="$(random_hex 24)"
jwt_secret="$(random_hex 32)"
jwt() {
  python3 - "$jwt_secret" "$1" <<'PY'
import base64, hashlib, hmac, json, sys, time
secret, role = sys.argv[1:]
encode = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
body = f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode({'role': role, 'iss': 'motionly-db-test', 'exp': int(time.time()) + 3600})}"
signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
print(body + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode())
PY
}

# The postgres image's entrypoint reads the password file after dropping to
# the postgres user, so the file (in a private directory) is world-readable.
printf '%s' "$postgres_password" >"$work/postgres_password"
chmod 0644 "$work/postgres_password"

# Environment files keep secrets out of `docker run` argv. Auth, REST and
# Storage get no database password here: they read their own.
cat >"$work/auth.env" <<EOF
GOTRUE_API_HOST=127.0.0.1
GOTRUE_API_PORT=9999
GOTRUE_DB_DRIVER=postgres
GOTRUE_SITE_URL=http://localhost
GOTRUE_JWT_SECRET=$jwt_secret
GOTRUE_JWT_AUD=authenticated
GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated
GOTRUE_JWT_ADMIN_ROLES=service_role
GOTRUE_DISABLE_SIGNUP=true
API_EXTERNAL_URL=http://localhost/supabase
EOF
cat >"$work/rest.env" <<EOF
PGRST_DB_URI=@/var/lib/motionly-db-credentials/db-uri
PGRST_DB_SCHEMAS=public,storage,graphql_public
PGRST_DB_ANON_ROLE=anon
PGRST_JWT_SECRET=$jwt_secret
PGRST_SERVER_PORT=3000
PGRST_ADMIN_SERVER_PORT=3001
EOF
cat >"$work/storage.env" <<EOF
ANON_KEY=$(jwt anon)
SERVICE_KEY=$(jwt service_role)
POSTGREST_URL=http://127.0.0.1:3000
PGRST_JWT_SECRET=$jwt_secret
FILE_SIZE_LIMIT=536870912
STORAGE_BACKEND=file
FILE_STORAGE_BACKEND_PATH=/var/lib/storage
GLOBAL_S3_BUCKET=motionly-media
SERVER_HOST=127.0.0.1
EOF
cat >"$work/realtime.env" <<EOF
PORT=4000
DB_HOST=postgres
DB_PORT=5432
DB_USER=supabase_admin
DB_PASSWORD=$postgres_password
DB_NAME=postgres
DB_SSL=true
DB_SSL_CA_CERT=/etc/motionly-db-tls/ca/ca.crt
DB_AFTER_CONNECT_QUERY=SET search_path TO _realtime
DB_ENC_KEY=$(random_hex 8)
API_JWT_SECRET=$jwt_secret
SECRET_KEY_BASE=$(random_hex 32)
METRICS_JWT_SECRET=$(random_hex 32)
APP_NAME=realtime
SEED_SELF_HOST=true
RUN_JANITOR=true
ERL_AFLAGS=-proto_dist inet_tcp
DNS_NODES=''
RLIMIT_NOFILE=100000
EOF

wait_for() {
  local what="$1" tries="$2"
  shift 2
  for _ in $(seq 1 "$tries"); do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "$what did not become ready" >&2
  return 1
}

echo "db-tls: $postgres_image"
for volume in "${volumes[@]}"; do docker volume create "$volume" >/dev/null; done
docker run --rm --network none \
  -v "$tls_ca_key:/var/lib/motionly-db-tls/ca-key" \
  -v "$tls_ca:/var/lib/motionly-db-tls/ca" \
  -v "$tls_server:/var/lib/motionly-db-tls/server" \
  -v "$root/infra/db/generate-tls.sh:/opt/motionly-db/generate-tls.sh:ro" \
  --entrypoint /bin/sh "$postgres_image" /opt/motionly-db/generate-tls.sh

echo "postgres: $postgres_image"
# The same TLS and client-authentication settings as the postgres service.
docker run -d --name "$db" \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password -e POSTGRES_DB=postgres \
  -v "$work/postgres_password:/run/secrets/postgres_password:ro" \
  -v "$tls_server:/etc/motionly-db-tls/server:ro" \
  -v "$root/infra/db/pg_hba.conf:/etc/motionly-db/pg_hba.conf:ro" \
  -v "$root/infra/db/roles.sql:/docker-entrypoint-initdb.d/init-scripts/99-roles.sql:ro" \
  -v "$root/infra/db/realtime.sql:/docker-entrypoint-initdb.d/init-scripts/98-realtime.sql:ro" \
  "$postgres_image" postgres -D /etc/postgresql \
  -c ssl=on \
  -c ssl_cert_file=/etc/motionly-db-tls/server/server.crt \
  -c ssl_key_file=/etc/motionly-db-tls/server/server.key \
  -c ssl_min_protocol_version=TLSv1.2 \
  -c hba_file=/etc/motionly-db/pg_hba.conf >/dev/null
# TCP, not the socket: first-boot init runs a socket-only server.
wait_for postgres 90 docker exec "$db" pg_isready -U postgres -h 127.0.0.1
# The image restarts once after init; make sure the final server is up.
sleep 3
wait_for postgres 30 docker exec "$db" pg_isready -U postgres -h 127.0.0.1
# Containers joining this network namespace share its hosts file: `postgres`
# is the container's own network address, as the service name is on the host.
docker exec -u root "$db" sh -c 'echo "$(hostname -i | cut -d" " -f1) postgres" >> /etc/hosts'

joined() {
  docker run --network "container:$db" -v "$tls_ca:/etc/motionly-db-tls/ca:ro" "$@"
}

echo "db-roles: $postgres_image"
joined --rm \
  -v "$auth_credentials:/var/lib/motionly-db-credentials/auth" \
  -v "$rest_credentials:/var/lib/motionly-db-credentials/rest" \
  -v "$storage_credentials:/var/lib/motionly-db-credentials/storage" \
  -v "$root/infra/db/apply-role-passwords.sh:/opt/motionly-db/apply-role-passwords.sh:ro" \
  --entrypoint /bin/sh "$postgres_image" /opt/motionly-db/apply-role-passwords.sh

echo "auth: $auth_image"
joined -d --name "$prefix-auth" --env-file "$work/auth.env" \
  -v "$auth_credentials:/var/lib/motionly-db-credentials:ro" \
  -v "$root/infra/db/start-auth.sh:/opt/motionly-db/start-auth.sh:ro" \
  --entrypoint /bin/sh "$auth_image" /opt/motionly-db/start-auth.sh >/dev/null
echo "rest: $rest_image"
joined -d --name "$prefix-rest" --env-file "$work/rest.env" \
  -v "$rest_credentials:/var/lib/motionly-db-credentials:ro" "$rest_image" >/dev/null
echo "storage: $storage_image"
joined -d --name "$prefix-storage" --env-file "$work/storage.env" \
  -v "$storage_credentials:/var/lib/motionly-db-credentials:ro" \
  -v "$root/infra/db/start-storage.sh:/opt/motionly-db/start-storage.sh:ro" \
  --entrypoint /bin/sh "$storage_image" /opt/motionly-db/start-storage.sh >/dev/null
echo "realtime: $realtime_image"
joined -d --name "$prefix-realtime" --env-file "$work/realtime.env" "$realtime_image" >/dev/null

fail_with_logs() {
  docker logs --tail 40 "$prefix-$1" >&2 || true
  return 1
}
http_ok() {
  docker exec "$db" python3 -c 'import sys, urllib.request; urllib.request.urlopen(urllib.request.Request(sys.argv[1], headers={"Host": sys.argv[2]}), timeout=5)' "$1" "${2:-127.0.0.1}"
}
wait_for auth 60 http_ok http://127.0.0.1:9999/health || fail_with_logs auth
wait_for rest 60 http_ok http://127.0.0.1:3001/ready || fail_with_logs rest
wait_for storage 60 http_ok http://127.0.0.1:5000/status || fail_with_logs storage
wait_for realtime 90 http_ok http://127.0.0.1:4000/api/ping realtime-dev.supabase-realtime \
  || fail_with_logs realtime

# The rules the setup must enforce, from outside the database container.
client_check() {
  local expectation="$1" label="$2"
  shift 2
  local status=0
  joined --rm -v "$work/postgres_password:/run/secrets/postgres_password:ro" "$@" \
    --entrypoint /bin/sh "$postgres_image" -c \
    'PGPASSWORD="$(cat /run/secrets/postgres_password)" psql -X -q -At -h postgres -d postgres -c "select 1"' \
    >"$work/client-check.log" 2>&1 || status=$?
  if [[ "$expectation" == accepted && "$status" -ne 0 ]] || [[ "$expectation" == refused && "$status" -eq 0 ]]; then
    echo "connection check failed: $label should be $expectation" >&2
    cat "$work/client-check.log" >&2
    exit 1
  fi
  echo "connection check: $label is $expectation"
}
client_check accepted "a verified TLS login as postgres" \
  -e PGUSER=postgres -e PGSSLMODE=verify-full -e PGSSLROOTCERT=/etc/motionly-db-tls/ca/ca.crt
client_check refused "a password login without TLS" \
  -e PGUSER=postgres -e PGSSLMODE=disable
client_check refused "the superuser password for the REST role" \
  -e PGUSER=authenticator -e PGSSLMODE=verify-full -e PGSSLROOTCERT=/etc/motionly-db-tls/ca/ca.crt

docker exec -u root "$db" sh -c 'rm -rf /opt/motionly-db && mkdir -p /opt/motionly-db'
docker cp "$root/supabase/migrations" "$db:/opt/motionly-db/migrations"
docker cp "$root/supabase/tests" "$db:/opt/motionly-db/tests"
# Migrations run like the db-migrate service: over TLS, verified, as postgres.
joined --rm \
  -e PGHOST=postgres -e PGSSLMODE=verify-full -e PGSSLROOTCERT=/etc/motionly-db-tls/ca/ca.crt \
  -v "$work/postgres_password:/run/secrets/postgres_password:ro" \
  -v "$root/supabase/migrations:/opt/motionly-db/migrations:ro" \
  -v "$root/infra/db/migrate.sh:/opt/motionly-db/migrate.sh:ro" \
  --entrypoint /bin/sh "$postgres_image" /opt/motionly-db/migrate.sh

docker exec "$db" psql -U supabase_admin -h 127.0.0.1 -d postgres -X -q -v ON_ERROR_STOP=1 \
  -c "create extension if not exists pgtap with schema extensions" >/dev/null

if [[ "$#" -gt 0 ]]; then
  tests=("$@")
else
  mapfile -t tests < <(cd "$root/supabase/tests" && ls ./*.sql | LC_ALL=C sort)
fi
failed=0
passed_assertions=0
for test in "${tests[@]}"; do
  name="$(basename "$test")"
  out="$work/${name%.sql}.tap"
  status=0
  docker exec -e PGOPTIONS='-c search_path=public,extensions' "$db" \
    psql -U postgres -h 127.0.0.1 -d postgres -X -At -v ON_ERROR_STOP=1 \
    -f "/opt/motionly-db/tests/$name" >"$out" 2>&1 || status=$?
  ok="$(grep -cE '^ok ' "$out" || true)"
  not_ok="$(grep -cE '^not ok ' "$out" || true)"
  planned="$(grep -oE '^1\.\.[0-9]+' "$out" | head -n 1 | cut -c4- || true)"
  passed_assertions=$((passed_assertions + ok))
  if [[ "$status" -ne 0 || "$not_ok" -ne 0 || "$planned" != "$ok" ]]; then
    failed=$((failed + 1))
    echo "FAIL $name (exit $status, planned ${planned:-?}, ok $ok, not ok $not_ok)" >&2
    grep -m 6 -E 'ERROR|^not ok|# +(got|want|Failed)' "$out" | sed 's/^/    /' >&2 || true
  fi
done
echo "pgTAP against $postgres_image: ${#tests[@]} files, $passed_assertions assertions passed, $failed files failed"
[[ "$failed" -eq 0 ]]
