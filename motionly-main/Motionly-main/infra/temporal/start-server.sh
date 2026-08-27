#!/bin/sh
set -eu

# The custom wrapper reads the database password and renders a private 0600
# configuration itself. Keep PEM keys as Docker secret files; never export or
# interpolate certificate material in this shell process.
for secret in temporal_db_password temporal_ca_cert temporal_server_cert temporal_server_key temporal_internode_cert temporal_internode_key; do
  test -s "/run/secrets/$secret" || {
    echo "missing required Temporal secret: $secret" >&2
    exit 78
  }
done
: "${MOTIONLY_TEMPORAL_NAMESPACE:?MOTIONLY_TEMPORAL_NAMESPACE is required}"
: "${MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL:?MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL is required}"
exec motionly-temporal-server start \
  --config-template /opt/motionly-temporal/config/production.yaml.tmpl
