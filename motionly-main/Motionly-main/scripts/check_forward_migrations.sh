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

# A new migration must sort after every migration on the base ref and use the
# name infra/db/migrate.sh accepts. Both the Supabase CLI and migrate.sh refuse
# to apply a pending migration older than the latest applied one, so a late
# version would pass review and then fail the release.
base_latest="$(git ls-tree --name-only "$base_ref" -- "$canonical_dir/" 2>/dev/null \
  | xargs -r -n1 basename | grep -E '^[0-9]{12}_' | LC_ALL=C sort | tail -n 1 | cut -d_ -f1 || true)"
ordering_violations=""
while IFS=$'\t' read -r status path rest; do
  [[ "$status" == "A" ]] || continue
  file="$(basename "$path")"
  if [[ ! "$file" =~ ^[0-9]{12}_[a-z0-9_]+\.sql$ ]]; then
    ordering_violations+="invalid name (want <12-digit version>_<lower_snake_name>.sql): $path"$'\n'
  elif [[ -n "$base_latest" && ! "${file%%_*}" > "$base_latest" ]]; then
    ordering_violations+="version not after $base_latest on $base_ref: $path"$'\n'
  fi
done <<<"$canonical_changes"
if [[ -n "$ordering_violations" ]]; then
  echo "New migrations must be named and ordered after the base branch:" >&2
  printf '%s' "$ordering_violations" >&2
  exit 1
fi

deprecated_additions="$(changes "$deprecated_dir" | awk '$1 == "A" { print }')"
if [[ -n "$deprecated_additions" ]]; then
  echo "$deprecated_dir is deprecated; use $canonical_dir instead:" >&2
  echo "$deprecated_additions" >&2
  exit 1
fi

echo "Migration history is forward-only."
