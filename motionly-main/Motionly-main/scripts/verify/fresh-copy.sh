#!/usr/bin/env bash
# Run `pnpm verify` against a fresh copy of the repository.
#
# The copy contains exactly the files git would hand to a new clone of the
# current working tree: tracked files plus untracked files that are not ignored.
# Nothing ignored (node_modules, .env, virtualenvs, build output, caches) is
# copied, so a check that silently depends on local state fails here.
#
#   scripts/verify/fresh-copy.sh                 # stages: static,unit
#   scripts/verify/fresh-copy.sh static,unit,media
set -euo pipefail

stages="${1:-static,unit}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
git_root="$(git -C "$repo_root" rev-parse --show-toplevel)"
prefix="$(realpath --relative-to="$git_root" "$repo_root")"
python_bin="${MOTIONLY_PYTHON:-/usr/bin/python3.12}"
work="$(mktemp -d "${TMPDIR:-/tmp}/motionly-fresh-copy-XXXXXX")"
trap 'rm -rf "$work"' EXIT

echo "fresh copy: $work (stages: $stages)"
(
  cd "$git_root"
  git ls-files -z --cached --others --exclude-standard -- "$prefix" |
    while IFS= read -r -d '' path; do
      [[ -f "$path" ]] || continue # tracked but deleted in the working tree
      relative="${path#"$prefix"/}"
      mkdir -p "$work/repo/$(dirname "$relative")"
      cp -p "$path" "$work/repo/$relative"
    done
)
cd "$work/repo"
git init -q && git add -A && git -c user.email=verify@motionly.test -c user.name=verify commit -q -m "fresh copy"

if ! "$python_bin" -m venv "$work/venv" >/dev/null 2>&1; then
  # Some distro Pythons ship without ensurepip; bootstrap pip from a host pip.
  rm -rf "$work/venv"
  "$python_bin" -m venv --without-pip "$work/venv"
  pip3 --python "$work/venv/bin/python" install --quiet --disable-pip-version-check "pip==25.2"
fi
"$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "pip==25.2"
"$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
  --require-hashes -r apps/api/requirements-dev.lock
corepack pnpm@"$(node -p "require('./package.json').packageManager.split('@')[1]")" \
  install --frozen-lockfile >/dev/null

MOTIONLY_PYTHON="$work/venv/bin/python" node scripts/verify/verify.mjs --stage "$stages"
status=$?
mkdir -p "$repo_root/test-results/fresh-copy"
cp -r "$work/repo/test-results/verify" "$repo_root/test-results/fresh-copy/" 2>/dev/null || true
exit "$status"
