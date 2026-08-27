#!/usr/bin/env bash
# Validate evidence required before a same-VM Temporal mTLS/authz canary.
# This tool intentionally has no Compose, Docker, cloud, or migration command.
set -euo pipefail

usage() {
  echo "Usage: $0 --release-dir <verified-release-dir> --overlap-proof <evidence-file>" >&2
  exit 64
}

release_dir=""
overlap_proof=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --release-dir) [[ "$#" -ge 2 ]] || usage; release_dir="$2"; shift 2 ;;
    --overlap-proof) [[ "$#" -ge 2 ]] || usage; overlap_proof="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -d "$release_dir" && -f "$release_dir/release-manifest.json" ]] || {
  echo "refusing Temporal canary: verified signed release directory is required" >&2
  exit 78
}
[[ -f "$release_dir/release-manifest.sig" && -f "$release_dir/release-manifest.pem" ]] || {
  echo "refusing Temporal canary: signed release manifest evidence is incomplete" >&2
  exit 78
}
[[ -n "$overlap_proof" && -f "$overlap_proof" ]] || {
  echo "refusing automatic Temporal activation/migration: dual frontend overlap proof is not configured" >&2
  exit 78
}

# Evidence is produced by a separately approved, controlled canary procedure;
# it must not be synthesized by this release agent.
for gate in \
  signed_release_verified \
  preflight_verified \
  mtls_handshake_verified \
  authorization_role_matrix_verified \
  controlled_namespace_validation_verified \
  rollback_verified \
  dual_frontend_overlap_verified; do
  grep -qx "${gate}=true" "$overlap_proof" || {
    echo "refusing Temporal canary: missing ${gate} evidence" >&2
    exit 78
  }
done

echo "Temporal canary evidence is complete. This gate performs no activation or migration." >&2
