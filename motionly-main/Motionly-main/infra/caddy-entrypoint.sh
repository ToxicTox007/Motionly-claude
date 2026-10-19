#!/bin/sh
# Ingress entrypoint behind CloudFront: read the origin-guard secrets from their
# Compose secret files, refuse to start with a missing or unsafe value, and
# export them for infra/Caddyfile.origin-guard. A trailing newline in a file is
# ignored. The previous secret is optional (empty outside a rotation).
#
# Usage: caddy-entrypoint.sh <command> [args...]
set -eu

directory="${ORIGIN_GUARD_SECRETS_DIR:-/run/secrets}"

valid() {
  [ "${#1}" -ge 32 ] && [ "${#1}" -le 256 ] && printf '%s' "$1" | grep -Eq '^[A-Za-z0-9_-]+$'
}

read_secret() {
  if [ -f "$directory/$1" ]; then
    # Command substitution drops trailing newlines.
    printf '%s' "$(cat "$directory/$1")"
  fi
}

current="$(read_secret edge_origin_verify)"
previous="$(read_secret edge_origin_verify_previous)"

if ! valid "$current"; then
  echo "origin guard: edge_origin_verify must be 32-256 characters of A-Z a-z 0-9 _ -" >&2
  exit 1
fi
if [ -n "$previous" ] && ! valid "$previous"; then
  echo "origin guard: edge_origin_verify_previous must be empty or 32-256 characters of A-Z a-z 0-9 _ -" >&2
  exit 1
fi

export MOTIONLY_ORIGIN_VERIFY_CURRENT="$current"
# An empty previous value can never match: the guard also requires the format.
export MOTIONLY_ORIGIN_VERIFY_PREVIOUS="$previous"
exec "$@"
