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
# Ringpop advertises an IP address, not a host name. Every Temporal service runs
# in this one container, so its first address is reachable by all of them.
broadcast_address="$(hostname -i | awk '{print $1}')"
case "$broadcast_address" in
  "" | *[!0-9.]*)
    echo "cannot determine this container's IPv4 address for Temporal membership" >&2
    exit 78
    ;;
esac
template="$(mktemp)"
sed "s/{{TEMPORAL_BROADCAST_ADDRESS}}/$broadcast_address/" \
  /opt/motionly-temporal/config/production.yaml.tmpl > "$template"
exec motionly-temporal-server start --config-template "$template"
