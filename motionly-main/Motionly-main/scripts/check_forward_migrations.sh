#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if ! git rev-parse --verify "$base_ref" >/dev/null 2>&1; then
  base_ref="HEAD^"
fi

canonical_dir="supabase/migrations"
archived_dir="infra/migrations_legacy"
deprecated_dir="infra/migrations"

violations="$({ git diff --name-status "$base_ref"...HEAD -- "$canonical_dir" || true; } | awk '$1 != "A" { print }')"
if [[ -n "$violations" ]]; then
  echo "Applied migrations are immutable; add a forward-fix migration instead:" >&2
  echo "$violations" >&2
  exit 1
fi

legacy_additions="$({ git diff --name-status "$base_ref"...HEAD -- "$archived_dir" || true; } | awk '$1 == "A" && $2 ~ /\.sql$/ { print }')"
if [[ -n "$legacy_additions" ]]; then
  echo "New migrations must go in $canonical_dir, not the archived legacy tree:" >&2
  echo "$legacy_additions" >&2
  exit 1
fi

deprecated_additions="$({ git diff --name-status "$base_ref"...HEAD -- "$deprecated_dir" || true; } | awk '$1 == "A" { print }')"
if [[ -n "$deprecated_additions" ]]; then
  echo "$deprecated_dir is deprecated; use $canonical_dir instead:" >&2
  echo "$deprecated_additions" >&2
  exit 1
fi

echo "Migration history is forward-only."
