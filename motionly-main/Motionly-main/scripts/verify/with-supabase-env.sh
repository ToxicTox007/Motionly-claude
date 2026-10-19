#!/usr/bin/env bash
# Run a command with the local Supabase stack's connection settings exported:
# SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
# SUPABASE_JWT_SECRET and DATABASE_URL.
#
# Usage: scripts/verify/with-supabase-env.sh <command> [args...]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
status_env="$("$root/node_modules/.bin/supabase" status --workdir "$root" -o env)"

# Accept only plain KEY="value" lines, so nothing else is evaluated.
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=\"([^\"\$\`\\]*)\"$ ]]; then
    printf -v "${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
  fi
done <<<"$status_env"

for name in API_URL ANON_KEY SERVICE_ROLE_KEY JWT_SECRET DB_URL; do
  if [[ -z "${!name:-}" ]]; then
    echo "with-supabase-env: 'supabase status' did not report $name; is the stack running?" >&2
    exit 1
  fi
done

export SUPABASE_URL="$API_URL"
export SUPABASE_ANON_KEY="$ANON_KEY"
export SUPABASE_SERVICE_ROLE_KEY="$SERVICE_ROLE_KEY"
export SUPABASE_JWT_SECRET="$JWT_SECRET"
export DATABASE_URL="$DB_URL"
exec "$@"
