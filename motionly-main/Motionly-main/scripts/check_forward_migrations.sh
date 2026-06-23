#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if ! git rev-parse --verify "$base_ref" >/dev/null 2>&1; then
  base_ref="HEAD^"
fi

violations="$({ git diff --name-status "$base_ref"...HEAD -- infra/migrations || true; } | awk '$1 != "A" { print }')"
if [[ -n "$violations" ]]; then
  echo "Applied migrations are immutable; add a forward-fix migration instead:" >&2
  echo "$violations" >&2
  exit 1
fi

echo "Migration history is forward-only."
