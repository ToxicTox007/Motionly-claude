#!/usr/bin/env bash
# Apply an immutable Motionly release on the single approved Azure VM host.
# This command takes only a non-secret release SHA. The VM managed identity reads
# release artifacts from Blob Storage and credentials from Key Vault locally.
set -euo pipefail
umask 077

usage() {
  echo "Usage: $0 --release-id <git-sha> [--preflight] | --rollback" >&2
  exit 64
}

release_id=""
mode="apply"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --release-id)
      [[ "$#" -ge 2 ]] || usage
      release_id="$2"
      shift 2
      ;;
    --preflight)
      mode="preflight"
      shift
      ;;
    --rollback)
      mode="rollback"
      shift
      ;;
    *) usage ;;
  esac
done
[[ "$mode" == "rollback" || "$release_id" =~ ^[0-9a-f]{7,64}$ ]] || usage

host_config="/etc/motionly/host.env"
[[ -r "$host_config" ]] || {
  echo "Missing root-owned host configuration: $host_config" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$host_config"

for value in MOTIONLY_RELEASE_ROOT MOTIONLY_AGENT_ROOT MOTIONLY_KEY_VAULT_NAME MOTIONLY_RELEASE_STORAGE_ACCOUNT MOTIONLY_RELEASE_CONTAINER MOTIONLY_GHCR_USERNAME MOTIONLY_COSIGN_IDENTITY_REGEXP MOTIONLY_APP_DOMAIN MOTIONLY_API_DOMAIN MOTIONLY_SUPABASE_DOMAIN MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL; do
  [[ -n "${!value:-}" ]] || {
    echo "$value is required in $host_config" >&2
    exit 78
  }
done
[[ "$MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE" =~ ^temporalio/admin-tools@sha256:[a-f0-9]{64}$ ]] || {
  echo "TEMPORAL_ADMIN_TOOLS_IMAGE must be a digest-pinned upstream Temporal admin-tools image" >&2
  exit 78
}
[[ "$MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]] || {
  echo "MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL must be an RFC3339 timestamp" >&2
  exit 78
}
for command in az docker cosign python3 tar install; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required by the release agent" >&2
    exit 127
  }
done
docker compose version >/dev/null

releases_dir="$MOTIONLY_RELEASE_ROOT/releases"
manifests_dir="$MOTIONLY_RELEASE_ROOT/manifests"
current_link="$MOTIONLY_RELEASE_ROOT/current"
previous_link="$MOTIONLY_RELEASE_ROOT/previous"
mkdir -p -m 700 "$releases_dir" "$manifests_dir"

blob_download() {
  local blob_name="$1"
  local destination="$2"
  az storage blob download \
    --auth-mode login \
    --only-show-errors \
    --account-name "$MOTIONLY_RELEASE_STORAGE_ACCOUNT" \
    --container-name "$MOTIONLY_RELEASE_CONTAINER" \
    --name "$release_id/$blob_name" \
    --file "$destination" \
    --overwrite false >/dev/null
}

validate_release() {
  local release_dir="$1"
  local manifest="$release_dir/release-manifest.json"
  "$MOTIONLY_AGENT_ROOT/bin/generate-release-manifest" --validate "$manifest"
  cosign verify-blob \
    --certificate "$release_dir/release-manifest.pem" \
    --signature "$release_dir/release-manifest.sig" \
    --certificate-identity-regexp "$MOTIONLY_COSIGN_IDENTITY_REGEXP" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    "$manifest" >/dev/null
  python3 - "$manifest" "$release_id" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
revision = manifest.get("revision")
release_identity = manifest.get("release_id", revision)
if revision != sys.argv[2] or release_identity != sys.argv[2]:
    raise SystemExit("signed release manifest does not match requested release ID")
PY
}

validate_release_bundle() {
  local release_dir="$1"
  "$MOTIONLY_AGENT_ROOT/bin/generate-release-manifest" \
    --validate-bundle \
    --manifest "$release_dir/release-manifest.json" \
    --bundle "$release_dir/release-bundle.tar.gz"
}

fetch_release() {
  local release_dir="$releases_dir/$release_id"
  if [[ -e "$release_dir" ]]; then
    [[ -f "$release_dir/release-manifest.json" && -f "$release_dir/release-bundle.tar.gz" ]] || {
      echo "existing release directory is incomplete: $release_dir" >&2
      exit 65
    }
    validate_release "$release_dir"
    validate_release_bundle "$release_dir"
    [[ -f "$release_dir/bundle/infra/docker-compose.gpu.yml" ]] || {
      echo "existing release directory lacks extracted Compose configuration" >&2
      exit 65
    }
    printf '%s\n' "$release_dir"
    return
  fi

  local scratch_dir
  scratch_dir="$(mktemp -d "$releases_dir/.${release_id}.XXXXXX")"
  local success=false
  cleanup_release_scratch() {
    if [[ "$success" != true ]]; then
      rm -rf "$scratch_dir"
    fi
  }
  trap cleanup_release_scratch RETURN

  blob_download release-manifest.json "$scratch_dir/release-manifest.json"
  blob_download release-manifest.sig "$scratch_dir/release-manifest.sig"
  blob_download release-manifest.pem "$scratch_dir/release-manifest.pem"
  # Authenticate the manifest before retrieving or unpacking the larger archive.
  validate_release "$scratch_dir"
  blob_download release-bundle.tar.gz "$scratch_dir/release-bundle.tar.gz"
  # Check compressed bytes and canonical extracted-file inventory before tar extraction.
  validate_release_bundle "$scratch_dir"
  mkdir -m 700 "$scratch_dir/bundle"
  tar -xzf "$scratch_dir/release-bundle.tar.gz" -C "$scratch_dir/bundle"
  [[ -f "$scratch_dir/bundle/infra/docker-compose.gpu.yml" ]] || {
    echo "release bundle lacks production Compose configuration" >&2
    exit 65
  }
  if ! mv "$scratch_dir" "$release_dir"; then
    echo "release directory already exists; refusing to replace it" >&2
    exit 65
  fi
  success=true
  printf '%s\n' "$release_dir"
}

fetch_secret_to_file() {
  local key_vault_name="$1"
  local destination="$2"
  local required="${3:-required}"
  local temporary="${destination}.tmp"
  if [[ "$required" == required ]]; then
    az keyvault secret show --only-show-errors --vault-name "$MOTIONLY_KEY_VAULT_NAME" \
      --name "$key_vault_name" --query value --output tsv > "$temporary"
  elif ! az keyvault secret show --only-show-errors --vault-name "$MOTIONLY_KEY_VAULT_NAME" \
      --name "$key_vault_name" --query value --output tsv > "$temporary" 2>/dev/null; then
    : > "$temporary"
  fi
  python3 - "$temporary" "$destination" <<'PY'
from pathlib import Path
import sys
source, destination = map(Path, sys.argv[1:])
value = source.read_text(encoding="utf-8").rstrip("\n")
if "\n" in value or "\r" in value:
    raise SystemExit("Key Vault secret values must be single-line")
destination.write_text(value + "\n", encoding="utf-8")
destination.chmod(0o600)
source.unlink(missing_ok=True)
PY
}

# PEM certificates and keys are base64 encoded in Key Vault so multiline material
# never enters shell variables, command arguments, Compose interpolation, or logs.
fetch_base64_secret_to_file() {
  local key_vault_name="$1"
  local destination="$2"
  local temporary="${destination}.b64.tmp"
  az keyvault secret show --only-show-errors --vault-name "$MOTIONLY_KEY_VAULT_NAME" \
    --name "$key_vault_name" --query value --output tsv > "$temporary"
  python3 - "$temporary" "$destination" <<'PY'
import base64
from pathlib import Path
import sys
source, destination = map(Path, sys.argv[1:])
encoded = source.read_bytes().strip()
try:
    value = base64.b64decode(encoded, validate=True)
except Exception as error:
    raise SystemExit("Key Vault base64 secret is invalid") from error
if not value or b"\x00" in value:
    raise SystemExit("Key Vault base64 secret is empty or unsafe")
destination.write_bytes(value)
destination.chmod(0o600)
source.unlink(missing_ok=True)
PY
}

write_release_inputs() {
  local release_dir="$1"
  local manifest="$release_dir/release-manifest.json"
  local secrets_dir="$release_dir/secrets"
  local env_file="$release_dir/compose.env"
  mkdir -m 700 "$secrets_dir"

  # Keep secret bytes in protected files.  No secret value is captured in a
  # shell variable or passed to another process as an argument.
  fetch_secret_to_file postgres-password "$secrets_dir/postgres_password"
  fetch_secret_to_file redis-password "$secrets_dir/redis_password"
  fetch_secret_to_file temporal-db-password "$secrets_dir/temporal_db_password"
  fetch_secret_to_file supabase-service-role-key "$secrets_dir/supabase_service_role_key"
  fetch_secret_to_file supabase-jwt-secret "$secrets_dir/supabase_jwt_secret"
  fetch_secret_to_file supabase-anon-key "$secrets_dir/supabase_anon_key"
  fetch_secret_to_file admin-api-token "$secrets_dir/admin_api_token"
  fetch_secret_to_file worker-api-token "$secrets_dir/worker_api_token"
  fetch_secret_to_file publish-token-encryption-key "$secrets_dir/publish_token_encryption_key"
  fetch_secret_to_file publish-oauth-state-secret "$secrets_dir/publish_oauth_state_secret"
  fetch_secret_to_file groq-api-key "$secrets_dir/groq_api_key"
  fetch_secret_to_file openrouter-api-key "$secrets_dir/openrouter_api_key"
  fetch_secret_to_file runway-api-key "$secrets_dir/runway_api_key"
  fetch_secret_to_file azure-openai-api-key "$secrets_dir/azure_openai_api_key" optional
  fetch_secret_to_file metrics-bearer-token "$secrets_dir/metrics_bearer_token"
  fetch_secret_to_file sentry-dsn "$secrets_dir/sentry_dsn"
  for name in temporal-ca-cert temporal-server-cert temporal-server-key temporal-internode-cert temporal-internode-key temporal-api-cert temporal-api-key temporal-dispatcher-cert temporal-dispatcher-key temporal-worker-cert temporal-worker-key temporal-reconciler-cert temporal-reconciler-key temporal-namespace-bootstrap-cert temporal-namespace-bootstrap-key temporal-diagnostics-cert temporal-diagnostics-key; do
    fetch_base64_secret_to_file "$name" "$secrets_dir/${name//-/_}"
  done

  python3 - "$secrets_dir/redis_password" "$secrets_dir/redis_url" <<'PY'
from pathlib import Path
import sys
password = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not password or "\n" in password or "\r" in password:
    raise SystemExit("redis password is invalid")
target = Path(sys.argv[2])
target.write_text(f"redis://:{password}@redis:6379/0\n", encoding="utf-8")
target.chmod(0o600)
PY

  # Compose interpolation receives only non-secret release topology and image
  # metadata.  Services read secret values from /run/secrets at startup.
  python3 - "$manifest" "$env_file" "$secrets_dir" "${MOTIONLY_APP_DOMAIN:?}" "${MOTIONLY_API_DOMAIN:?}" "${MOTIONLY_SUPABASE_DOMAIN:?}" "${MOTIONLY_AZURE_OPENAI_ENDPOINT:-}" "$MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE" "$MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL" <<'PY'
import json
import os
import sys
from pathlib import Path
manifest_path, env_path, secrets_dir, app_domain, api_domain, supabase_domain, azure_endpoint, temporal_admin_tools_image, bootstrap_admin_until = sys.argv[1:]
manifest = json.load(open(manifest_path, encoding="utf-8"))
secret_root = Path(secrets_dir)
def secret(name: str) -> str:
    return (secret_root / name).read_text(encoding="utf-8").strip()
environment = {
    **manifest["images"],
    "TEMPORAL_ADMIN_TOOLS_IMAGE": temporal_admin_tools_image,
    "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": bootstrap_admin_until,
    "SECRETS_DIR": secrets_dir,
    # These upstream images do not support a *_FILE interface.  They are
    # read only from protected files in this process, never passed via argv.
    "POSTGRES_PASSWORD": secret("postgres_password"),
    "REDIS_PASSWORD": secret("redis_password"),
    "SUPABASE_JWT_SECRET": secret("supabase_jwt_secret"),
    "SUPABASE_ANON_KEY": secret("supabase_anon_key"),
    "SUPABASE_SERVICE_ROLE_KEY": secret("supabase_service_role_key"),
    "SENTRY_DSN": secret("sentry_dsn"),
    "APP_DOMAIN": app_domain,
    "API_DOMAIN": api_domain,
    "SUPABASE_DOMAIN": supabase_domain,
}
if azure_endpoint:
    environment["AZURE_OPENAI_ENDPOINT"] = azure_endpoint
for name, value in environment.items():
    if "\n" in value or "\r" in value:
        raise SystemExit(f"unsafe newline in {name}")
with open(env_path, "w", encoding="utf-8") as output:
    for name, value in environment.items():
        output.write(f"{name}={value}\n")
os.chmod(env_path, 0o600)
PY
}

login_ghcr() {
  az keyvault secret show --only-show-errors --vault-name "$MOTIONLY_KEY_VAULT_NAME" \
    --name ghcr-read-token --query value --output tsv \
    | docker login ghcr.io --username "$MOTIONLY_GHCR_USERNAME" --password-stdin >/dev/null
}

compose_for() {
  local release_dir="$1"
  shift
  docker compose --project-name motionly --env-file "$release_dir/compose.env" -f "$release_dir/bundle/infra/docker-compose.gpu.yml" "$@"
}

validate_temporal_canary_gate() {
  local release_dir="$1"
  "$MOTIONLY_AGENT_ROOT/bin/temporal-canary-gate" \
    --release-dir "$release_dir" \
    --overlap-proof "$MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE"
}

activate_release() {
  local release_dir="$1"
  compose_for "$release_dir" config -q
  compose_for "$release_dir" pull
  compose_for "$release_dir" up -d --wait --wait-timeout 240 --remove-orphans
  compose_for "$release_dir" exec -T api \
    python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8000/health/live').status == 200"
}

record_current_release() {
  local release_dir="$1"
  if [[ -L "$current_link" ]]; then
    ln -sfn "$(readlink -f "$current_link")" "$previous_link"
    cp "$manifests_dir/current.json" "$manifests_dir/previous.json" 2>/dev/null || true
  fi
  ln -sfn "$release_dir" "$current_link"
  cp "$release_dir/release-manifest.json" "$manifests_dir/current.json"
  chmod 600 "$manifests_dir/current.json"
}

rollback_previous_release() {
  [[ -L "$previous_link" ]] || {
    echo "No previous release is recorded for rollback" >&2
    exit 66
  }
  local previous_dir
  previous_dir="$(readlink -f "$previous_link")"
  activate_release "$previous_dir"
  ln -sfn "$previous_dir" "$current_link"
  cp "$previous_dir/release-manifest.json" "$manifests_dir/current.json"
  cp "$previous_dir/release-manifest.json" "$manifests_dir/previous.json"
  echo "Rollback activated release $(basename "$previous_dir")" >&2
}

if [[ "$mode" == "rollback" ]]; then
  login_ghcr
  rollback_previous_release
  exit 0
fi

release_dir="$(fetch_release)"
if [[ "$mode" == "preflight" ]]; then
  validate_temporal_canary_gate "$release_dir"
  echo "Validated signed release $release_id and Temporal canary evidence; no containers or Key Vault values were changed."
  exit 0
fi

validate_temporal_canary_gate "$release_dir"
write_release_inputs "$release_dir"
login_ghcr
if ! activate_release "$release_dir"; then
  echo "Release $release_id failed health verification; attempting recorded image rollback." >&2
  rollback_previous_release
  exit 70
fi
record_current_release "$release_dir"
echo "Activated immutable release $release_id" >&2
