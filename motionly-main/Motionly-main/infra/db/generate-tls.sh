#!/bin/sh
# TLS material for the postgres and temporal-postgres servers (database audit
# DB-21). Run by the one-shot db-tls service before either server starts.
#
# Three volumes, each mounted only where it is needed:
#   postgres-tls-ca-key  the internal CA's private key       (db-tls only)
#   postgres-tls-ca      the CA certificate                  (every database client)
#   postgres-tls-server  the server certificate and its key  (the two servers)
#
# The CA (ten years) and the server certificate (two years, names postgres and
# temporal-postgres) are created once and kept. The server certificate is
# re-issued by the same CA when it has less than 60 days left, so clients keep
# trusting it; the servers pick it up when they next restart (every release).
# Nothing here leaves the host; there is no secret to provision.
set -eu
umask 077

ca_key_dir="${MOTIONLY_DB_TLS_CA_KEY_DIR:-/var/lib/motionly-db-tls/ca-key}"
ca_dir="${MOTIONLY_DB_TLS_CA_DIR:-/var/lib/motionly-db-tls/ca}"
server_dir="${MOTIONLY_DB_TLS_SERVER_DIR:-/var/lib/motionly-db-tls/server}"
# The postgres user of the supabase/postgres image owns the server key.
server_owner="${MOTIONLY_DB_TLS_SERVER_OWNER:-101:102}"

ca_key="$ca_key_dir/ca.key"
ca_cert="$ca_dir/ca.crt"
server_key="$server_dir/server.key"
server_cert="$server_dir/server.crt"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

valid_for() {
  # True if the certificate exists and is valid for at least $2 more seconds.
  [ -s "$1" ] && openssl x509 -checkend "$2" -noout -in "$1" >/dev/null 2>&1
}

issue_ca=0
if [ ! -s "$ca_key" ] || ! valid_for "$ca_cert" $((365 * 86400)); then
  issue_ca=1
fi
if [ "$issue_ca" = 1 ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
    -days 3650 -subj "/CN=Motionly internal database CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$work/ca.key" -out "$work/ca.crt" 2>/dev/null
  install -m 0600 "$work/ca.key" "$ca_key"
  install -m 0644 "$work/ca.crt" "$ca_cert"
  echo "db-tls: issued a new internal database CA"
fi

if [ "$issue_ca" = 1 ] || ! valid_for "$server_cert" $((60 * 86400)) \
  || [ ! -s "$server_key" ] \
  || ! openssl verify -CAfile "$ca_cert" "$server_cert" >/dev/null 2>&1; then
  cat >"$work/server.ext" <<'EOF'
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:postgres,DNS:temporal-postgres,DNS:localhost,IP:127.0.0.1
EOF
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
    -subj "/CN=postgres" -keyout "$work/server.key" -out "$work/server.csr" 2>/dev/null
  openssl x509 -req -in "$work/server.csr" -CA "$ca_cert" -CAkey "$ca_key" \
    -set_serial "0x$(openssl rand -hex 16)" -days 730 -extfile "$work/server.ext" \
    -out "$work/server.crt" 2>/dev/null
  install -m 0600 "$work/server.key" "$server_key"
  install -m 0644 "$work/server.crt" "$server_cert"
  echo "db-tls: issued a new server certificate"
fi

# PostgreSQL refuses a key another user can read; clients only read the CA.
chown "$server_owner" "$server_key" "$server_cert"
chmod 0600 "$server_key"
chmod 0644 "$server_cert" "$ca_cert"
chmod 0755 "$ca_dir" "$server_dir"
chmod 0700 "$ca_key_dir"
echo "db-tls: server certificate valid until $(openssl x509 -enddate -noout -in "$server_cert" | cut -d= -f2)"
