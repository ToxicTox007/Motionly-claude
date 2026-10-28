#!/usr/bin/env bash
# Lint every GitHub Actions workflow with a pinned, checksum-verified actionlint.
set -euo pipefail
version="1.7.7"
sha256="023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/motionly-tools/actionlint-$version"
binary="$cache/actionlint"
if [[ ! -x "$binary" ]]; then
  mkdir -p "$cache"
  archive="$cache/actionlint.tgz"
  curl -fsSL -o "$archive" \
    "https://github.com/rhysd/actionlint/releases/download/v$version/actionlint_${version}_linux_amd64.tar.gz"
  echo "$sha256  $archive" | sha256sum --check --status || {
    echo "actionlint archive checksum mismatch" >&2
    rm -f "$archive"
    exit 1
  }
  tar -xzf "$archive" -C "$cache" actionlint
  rm -f "$archive"
fi
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
"$binary" -config-file .github/actionlint.yaml -shellcheck= .github/workflows/*.yml
# The git repository root sits above this monorepo; GitHub runs its workflows
# (the root copies of these, see scripts/verify/workflow_parity.py).
if compgen -G "../../.github/workflows/*.yml" >/dev/null; then
  "$binary" -config-file .github/actionlint.yaml -shellcheck= ../../.github/workflows/*.yml
fi
