#!/usr/bin/env bash
# Fails if a change since the base ref edits, renames or deletes an existing
# migration. New migrations are always allowed. The only permitted edits are the
# one-time repairs pinned by sha256 in scripts/migration-repairs.sha256.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

base_ref="${1:-origin/main}"
if ! git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
  base_ref="HEAD^"
fi
if ! git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
  echo "Cannot compare migrations: neither ${1:-origin/main} nor HEAD^ exists." >&2
  echo "Fetch the base branch (actions/checkout with fetch-depth: 0)." >&2
  exit 1
fi

canonical_dir="supabase/migrations"
deprecated_dir="infra/migrations"
repairs_file="scripts/migration-repairs.sha256"

# Paths are relative to this project even when it is below the git root.
changes() {
  git diff --relative --name-status "$base_ref"...HEAD -- "$1"
}

is_pinned_repair() {
  local path="$1" expected actual
  [[ -f "$repairs_file" ]] || return 1
  expected="$(awk -v path="$path" '$0 !~ /^#/ && NF == 2 && $2 == path { print $1 }' "$repairs_file")"
  [[ -n "$expected" ]] || return 1
  actual="$(git show "HEAD:./$path" | sha256sum | awk '{ print $1 }')"
  [[ "$actual" == "$expected" ]]
}

canonical_changes="$(changes "$canonical_dir")"
violations=""
while IFS=$'\t' read -r status path rest; do
  [[ -n "$status" ]] || continue
  [[ "$status" == "A" ]] && continue
  if [[ "$status" == "M" ]] && is_pinned_repair "$path"; then
    continue
  fi
  violations+="${status}"$'\t'"${path}${rest:+$'\t'$rest}"$'\n'
done <<<"$canonical_changes"
if [[ -n "$violations" ]]; then
  echo "Applied migrations are immutable; add a forward-fix migration instead:" >&2
  printf '%s' "$violations" >&2
  exit 1
fi

deprecated_additions="$(changes "$deprecated_dir" | awk '$1 == "A" { print }')"
if [[ -n "$deprecated_additions" ]]; then
  echo "$deprecated_dir is deprecated; use $canonical_dir instead:" >&2
  echo "$deprecated_additions" >&2
  exit 1
fi

echo "Migration history is forward-only."
