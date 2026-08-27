#!/bin/sh
set -eu

address="${TEMPORAL_ADDRESS:?TEMPORAL_ADDRESS is required}"
namespace="${TEMPORAL_NAMESPACE:?TEMPORAL_NAMESPACE is required}"
retention="${TEMPORAL_NAMESPACE_RETENTION:-7d}"
server_name="${TEMPORAL_SERVER_NAME:?TEMPORAL_SERVER_NAME is required}"

for secret in temporal_ca_cert temporal_namespace_bootstrap_cert temporal_namespace_bootstrap_key; do
  test -s "/run/secrets/$secret" || {
    echo "missing required Temporal bootstrap secret: $secret" >&2
    exit 78
  }
done

set -- --address "$address" --tls \
  --tls-ca-path /run/secrets/temporal_ca_cert \
  --tls-cert-path /run/secrets/temporal_namespace_bootstrap_cert \
  --tls-key-path /run/secrets/temporal_namespace_bootstrap_key \
  --tls-server-name "$server_name"

until temporal operator cluster health "$@" >/dev/null 2>&1; do
  sleep 2
done

if temporal operator namespace describe "$@" --namespace "$namespace" >/dev/null 2>&1; then
  exit 0
fi

temporal operator namespace create "$@" --namespace "$namespace" --retention "$retention"
