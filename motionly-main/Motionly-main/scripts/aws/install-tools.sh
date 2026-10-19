#!/usr/bin/env bash
# Installs the pinned static-validation tools for infra/aws into
# ${MOTIONLY_AWS_TOOLS:-/tmp/motionly-aws-tools}: cfn-lint and the AWS CLI
# (hash-locked, own virtualenv), cfn-guard and cosign (release binaries
# verified by SHA-256; cosign is the version the supply chain signs with).
# Idempotent; used by `pnpm verify --stage aws` and CI.
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
tools=${MOTIONLY_AWS_TOOLS:-/tmp/motionly-aws-tools}
python=${PYTHON:-python3}
guard_version=3.2.1
guard_sha256=8c66efb19c63e6c2bf26b9a41bbcf2f85baa8a937b01d350940194faaf64cf1d
cosign_version=v2.4.3
cosign_sha256=caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708

mkdir -p "$tools"
if [ ! -x "$tools/venv/bin/cfn-lint" ] || [ ! -x "$tools/venv/bin/aws" ]; then
  rm -rf "$tools/venv"
  # Some Python builds ship without ensurepip; bootstrap pip with the host's pip then.
  if ! "$python" -m venv "$tools/venv" 2>/dev/null; then
    "$python" -m venv --without-pip "$tools/venv"
    pip3 --python "$tools/venv/bin/python" install --quiet --disable-pip-version-check pip==25.2
  fi
  "$tools/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    --require-hashes -r "$root/infra/aws/tools/requirements.lock"
fi

if [ ! -x "$tools/cfn-guard" ] || ! "$tools/cfn-guard" --version | grep -q " $guard_version"; then
  archive="$tools/cfn-guard-$guard_version.tar.gz"
  curl -fsSL -o "$archive" \
    "https://github.com/aws-cloudformation/cloudformation-guard/releases/download/$guard_version/cfn-guard-v3-x86_64-linux-latest.tar.gz"
  echo "$guard_sha256  $archive" | sha256sum -c - >/dev/null
  tar -xzf "$archive" -C "$tools" --strip-components=1 cfn-guard-v3-x86_64-linux-latest/cfn-guard
  rm -f "$archive"
fi

if [ ! -x "$tools/cosign" ] || ! "$tools/cosign" version 2>/dev/null | grep -q "${cosign_version#v}"; then
  curl -fsSL -o "$tools/cosign.download" \
    "https://github.com/sigstore/cosign/releases/download/$cosign_version/cosign-linux-amd64"
  echo "$cosign_sha256  $tools/cosign.download" | sha256sum -c - >/dev/null
  chmod 0755 "$tools/cosign.download"
  mv "$tools/cosign.download" "$tools/cosign"
fi

"$tools/venv/bin/cfn-lint" --version
"$tools/venv/bin/aws" --version
"$tools/cosign" version 2>/dev/null | grep GitVersion
"$tools/cfn-guard" --version
