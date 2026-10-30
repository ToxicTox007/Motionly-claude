#!/usr/bin/env bash
# Apply an immutable Motionly release on the AWS instance (infra/aws). GitHub
# runs this over SSM Run Command with only a non-secret release id
# (scripts/deploy/run_on_instance.sh). The instance role reads the release from
# S3 and secrets from SSM Parameter Store under /motionly/<environment>/; no
# secret value is ever passed as an argument or written to the Compose env file
# except the few upstream images that cannot read files.
set -euo pipefail
# Command substitutions such as release_dir="$(fetch_release)" keep set -e.
shopt -s inherit_errexit
umask 077

usage() {
  echo "Usage: $0 --release-id <git-sha> [--preflight] | --rollback" >&2
  exit 64
}

refuse() {
  echo "$1" >&2
  exit 78
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

host_config="${MOTIONLY_HOST_CONFIG:-/etc/motionly/host.env}"
[[ -r "$host_config" ]] || refuse "Missing root-owned host configuration: $host_config"
# shellcheck disable=SC1090
source "$host_config"

for value in MOTIONLY_RELEASE_ROOT MOTIONLY_AGENT_ROOT MOTIONLY_ENVIRONMENT MOTIONLY_AWS_REGION MOTIONLY_RELEASE_BUCKET MOTIONLY_COMPUTE_PROFILE MOTIONLY_REGISTRY MOTIONLY_COSIGN_IDENTITY_REGEXP MOTIONLY_APP_DOMAIN MOTIONLY_BILLING_MODE MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL; do
  [[ -n "${!value:-}" ]] || refuse "$value is required in $host_config"
done
case "$MOTIONLY_BILLING_MODE" in
  disabled | demo) ;;
  *) refuse "MOTIONLY_BILLING_MODE must be disabled or demo" ;;
esac
case "$MOTIONLY_ENVIRONMENT" in
  production | staging | rehearsal) ;;
  *) refuse "MOTIONLY_ENVIRONMENT must be production, staging or rehearsal" ;;
esac
case "$MOTIONLY_COMPUTE_PROFILE" in
  gpu | cpu) ;;
  *) refuse "MOTIONLY_COMPUTE_PROFILE must be gpu or cpu" ;;
esac
# CPU-only production topology (docs/ops/compose-vm-deployment.md). base (the
# default) preserves today's behaviour exactly; gpu-free deploys the GPU-free
# remote overlay on top of gpu.yml for a ComputeProfile=cpu host. Validated here,
# before anything is written, so an unsupported combination is refused early.
MOTIONLY_CPU_TOPOLOGY="${MOTIONLY_CPU_TOPOLOGY:-base}"
MOTIONLY_LOCAL_STEMS="${MOTIONLY_LOCAL_STEMS:-disabled}"
case "$MOTIONLY_CPU_TOPOLOGY" in
  base | gpu-free) ;;
  *) refuse "MOTIONLY_CPU_TOPOLOGY must be base or gpu-free" ;;
esac
case "$MOTIONLY_LOCAL_STEMS" in
  enabled | disabled) ;;
  *) refuse "MOTIONLY_LOCAL_STEMS must be enabled or disabled" ;;
esac
if [[ "$MOTIONLY_CPU_TOPOLOGY" == gpu-free && "$MOTIONLY_COMPUTE_PROFILE" != cpu ]]; then
  refuse "MOTIONLY_CPU_TOPOLOGY=gpu-free requires MOTIONLY_COMPUTE_PROFILE=cpu"
fi
if [[ "$MOTIONLY_LOCAL_STEMS" == enabled && "$MOTIONLY_CPU_TOPOLOGY" != gpu-free ]]; then
  refuse "MOTIONLY_LOCAL_STEMS=enabled requires MOTIONLY_CPU_TOPOLOGY=gpu-free"
fi
# The local release rehearsal (scripts/release/rehearse_release.py) runs this
# agent against emulated AWS endpoints, a loopback registry, a test signing key
# and the CPU-stage overlay. A real environment refuses every one of these.
if [[ "$MOTIONLY_ENVIRONMENT" != rehearsal ]]; then
  [[ -z "${MOTIONLY_COSIGN_PUBLIC_KEY:-}" ]] || refuse "MOTIONLY_COSIGN_PUBLIC_KEY is only allowed in rehearsal"
  [[ -z "${MOTIONLY_COMPOSE_OVERLAY:-}" ]] || refuse "MOTIONLY_COMPOSE_OVERLAY is only allowed in rehearsal"
  [[ -z "${MOTIONLY_REHEARSAL_EXTRA_SECRETS:-}" ]] || refuse "MOTIONLY_REHEARSAL_EXTRA_SECRETS is only allowed in rehearsal"
  if env | grep -q '^COMPOSE_'; then
    refuse "COMPOSE_* environment overrides are only allowed in rehearsal"
  fi
  if env | grep -q '^AWS_ENDPOINT_URL'; then
    refuse "AWS endpoint overrides are only allowed in rehearsal"
  fi
  [[ ! "$MOTIONLY_REGISTRY" =~ ^(127\.0\.0\.1|localhost): ]] || refuse "a loopback registry is only allowed in rehearsal"
  [[ -n "${MOTIONLY_REGISTRY_USERNAME:-}" ]] || refuse "MOTIONLY_REGISTRY_USERNAME is required in $host_config"
fi
[[ "$MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE" =~ ^temporalio/admin-tools@sha256:[a-f0-9]{64}$ ]] \
  || refuse "TEMPORAL_ADMIN_TOOLS_IMAGE must be a digest-pinned upstream Temporal admin-tools image"
[[ "$MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]] \
  || refuse "MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL must be an RFC3339 timestamp"

for command in aws docker cosign python3 tar install; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required by the release agent" >&2
    exit 127
  }
done
docker compose version >/dev/null

export AWS_REGION="$MOTIONLY_AWS_REGION" AWS_DEFAULT_REGION="$MOTIONLY_AWS_REGION"
ssm_prefix="/motionly/${MOTIONLY_ENVIRONMENT}"
releases_dir="$MOTIONLY_RELEASE_ROOT/releases"
manifests_dir="$MOTIONLY_RELEASE_ROOT/manifests"
current_link="$MOTIONLY_RELEASE_ROOT/current"
previous_link="$MOTIONLY_RELEASE_ROOT/previous"
mkdir -p -m 700 "$releases_dir" "$manifests_dir"

s3_download() {
  local name="$1"
  local destination="$2"
  aws s3 cp --only-show-errors "s3://$MOTIONLY_RELEASE_BUCKET/$release_id/$name" "$destination"
}

validate_release() {
  local release_dir="$1"
  local manifest="$release_dir/release-manifest.json"
  "$MOTIONLY_AGENT_ROOT/bin/generate-release-manifest" --validate "$manifest"
  if [[ -n "${MOTIONLY_COSIGN_PUBLIC_KEY:-}" ]]; then
    # Rehearsal only (refused above otherwise): the test signing key.
    # The test key's signature is not in a transparency log.
    cosign verify-blob --key "$MOTIONLY_COSIGN_PUBLIC_KEY" --insecure-ignore-tlog=true \
      --signature "$release_dir/release-manifest.sig" "$manifest" >/dev/null
  else
    cosign verify-blob \
      --certificate "$release_dir/release-manifest.pem" \
      --signature "$release_dir/release-manifest.sig" \
      --certificate-identity-regexp "$MOTIONLY_COSIGN_IDENTITY_REGEXP" \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      "$manifest" >/dev/null
  fi
  python3 - "$manifest" "$release_id" "$MOTIONLY_REGISTRY" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
revision = manifest.get("revision")
release_identity = manifest.get("release_id", revision)
if revision != sys.argv[2] or release_identity != sys.argv[2]:
    raise SystemExit("signed release manifest does not match requested release ID")
registry = sys.argv[3].rstrip("/") + "/"
foreign = sorted(name for name, image in manifest["images"].items() if not image.startswith(registry))
if foreign:
    raise SystemExit(f"release images must come from {registry}: {', '.join(foreign)}")
PY
}

validate_release_bundle() {
  local release_dir="$1"
  "$MOTIONLY_AGENT_ROOT/bin/generate-release-manifest" \
    --validate-bundle \
    --manifest "$release_dir/release-manifest.json" \
    --bundle "$release_dir/release-bundle.tar.gz"
}

# A gpu-free host can only deploy a release built for the GPU-free overlay: its
# bundle must carry docker-compose.remote-ai.yml and its signed manifest must
# list the cpu-vision-worker image. Called in preflight (after the bundle is on
# disk) and in apply before any SSM read or secret write, so an unsupported
# release is refused before anything sensitive happens. base needs nothing new.
validate_release_topology() {
  local release_dir="$1"
  [[ "$MOTIONLY_CPU_TOPOLOGY" == gpu-free ]] || return 0
  local overlay="$release_dir/bundle/infra/docker-compose.remote-ai.yml"
  [[ -f "$overlay" ]] || {
    echo "MOTIONLY_CPU_TOPOLOGY=gpu-free requires the GPU-free overlay, but this release bundle lacks infra/docker-compose.remote-ai.yml" >&2
    exit 65
  }
  # The manifest is JSON; read it with python3 (no jq on the host).
  python3 - "$release_dir/release-manifest.json" <<'PY' || exit 65
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if "MOTIONLY_CPU_VISION_WORKER_IMAGE" not in manifest.get("images", {}):
    raise SystemExit(
        "MOTIONLY_CPU_TOPOLOGY=gpu-free requires MOTIONLY_CPU_VISION_WORKER_IMAGE "
        "in release-manifest.json images"
    )
PY
}

# Records this release's resolved topology next to its compose.env so rollback
# and restore reuse exactly what was applied. Writes only the three non-secret
# keys (no secret ever enters argv or this file). When gpu-free with local
# stems, appends CPU_VISION_WORKER_QUEUES to compose.env so cpu-vision-worker
# gives up audio_demucs (the Demucs worker serves it instead).
write_release_topology() {
  local release_dir="$1"
  local topology_env="$release_dir/topology.env"
  umask 177
  {
    printf 'MOTIONLY_COMPUTE_PROFILE=%s\n' "$MOTIONLY_COMPUTE_PROFILE"
    printf 'MOTIONLY_CPU_TOPOLOGY=%s\n' "$MOTIONLY_CPU_TOPOLOGY"
    printf 'MOTIONLY_LOCAL_STEMS=%s\n' "$MOTIONLY_LOCAL_STEMS"
  } >"$topology_env"
  umask 077
  chmod 600 "$topology_env"
  if [[ "$MOTIONLY_CPU_TOPOLOGY" == gpu-free && "$MOTIONLY_LOCAL_STEMS" == enabled ]]; then
    printf 'CPU_VISION_WORKER_QUEUES=%s\n' "visual_analysis,transcription,matte_extraction" \
      >>"$release_dir/compose.env"
    chmod 600 "$release_dir/compose.env"
  fi
}

# Runs in a command substitution; every step fails it explicitly as well
# (verification must never be skipped), removing any partial download.
fetch_release() {
  local release_dir="$releases_dir/$release_id"
  if [[ -e "$release_dir" ]]; then
    [[ -f "$release_dir/release-manifest.json" && -f "$release_dir/release-bundle.tar.gz" ]] || {
      echo "existing release directory is incomplete: $release_dir" >&2
      exit 65
    }
    validate_release "$release_dir" || exit 65
    validate_release_bundle "$release_dir" || exit 65
    [[ -f "$release_dir/bundle/infra/docker-compose.gpu.yml" ]] || {
      echo "existing release directory lacks extracted Compose configuration" >&2
      exit 65
    }
    printf '%s\n' "$release_dir"
    return
  fi

  local scratch_dir
  scratch_dir="$(mktemp -d "$releases_dir/.${release_id}.XXXXXX")"
  abandon() {
    rm -rf "$scratch_dir"
    echo "release $release_id was not accepted: $1" >&2
    exit 65
  }

  s3_download release-manifest.json "$scratch_dir/release-manifest.json" || abandon "manifest download failed"
  s3_download release-manifest.sig "$scratch_dir/release-manifest.sig" || abandon "signature download failed"
  s3_download release-manifest.pem "$scratch_dir/release-manifest.pem" || abandon "certificate download failed"
  # Authenticate the manifest before retrieving or unpacking the larger archive.
  validate_release "$scratch_dir" || abandon "manifest verification failed"
  s3_download release-bundle.tar.gz "$scratch_dir/release-bundle.tar.gz" || abandon "bundle download failed"
  # Check compressed bytes and canonical extracted-file inventory before tar extraction.
  validate_release_bundle "$scratch_dir" || abandon "bundle verification failed"
  mkdir -m 700 "$scratch_dir/bundle" || abandon "could not create the bundle directory"
  tar -xzf "$scratch_dir/release-bundle.tar.gz" -C "$scratch_dir/bundle" || abandon "bundle extraction failed"
  [[ -f "$scratch_dir/bundle/infra/docker-compose.gpu.yml" ]] \
    || abandon "release bundle lacks production Compose configuration"
  mv "$scratch_dir" "$release_dir" || abandon "release directory already exists; refusing to replace it"
  printf '%s\n' "$release_dir"
}

# Writes one SSM parameter's value to a file. The value goes from the AWS CLI's
# stdout straight to disk; it is never held in a shell variable or argv.
read_parameter() {
  local name="$1"
  local destination="$2"
  local required="${3:-required}"
  local errors="${destination}.err"
  if aws ssm get-parameter --with-decryption --name "$ssm_prefix/$name" \
    --query Parameter.Value --output text >"$destination" 2>"$errors"; then
    rm -f "$errors"
    return 0
  fi
  if [[ "$required" != required ]] && grep -q ParameterNotFound "$errors"; then
    : >"$destination"
    rm -f "$errors"
    return 0
  fi
  echo "could not read SSM parameter $ssm_prefix/$name:" >&2
  cat "$errors" >&2
  rm -f "$errors" "$destination"
  exit 69
}

fetch_secret_to_file() {
  local parameter="$1"
  local destination="$2"
  local required="${3:-required}"
  local temporary="${destination}.tmp"
  read_parameter "$parameter" "$temporary" "$required"
  python3 - "$temporary" "$destination" <<'PY'
from pathlib import Path
import sys
source, destination = map(Path, sys.argv[1:])
value = source.read_text(encoding="utf-8").rstrip("\n")
if "\n" in value or "\r" in value:
    raise SystemExit("SSM secret values must be single-line")
destination.write_text(value + "\n", encoding="utf-8")
destination.chmod(0o444)
source.unlink(missing_ok=True)
PY
}

# PEM certificates and keys are base64 encoded in SSM so multiline material
# never enters shell variables, command arguments, Compose interpolation, or logs.
fetch_base64_secret_to_file() {
  local parameter="$1"
  local destination="$2"
  local temporary="${destination}.b64.tmp"
  read_parameter "$parameter" "$temporary"
  python3 - "$temporary" "$destination" <<'PY'
import base64
from pathlib import Path
import sys
source, destination = map(Path, sys.argv[1:])
encoded = source.read_bytes().strip()
try:
    value = base64.b64decode(encoded, validate=True)
except Exception as error:
    raise SystemExit("SSM base64 secret is invalid") from error
if not value or b"\x00" in value:
    raise SystemExit("SSM base64 secret is empty or unsafe")
destination.write_bytes(value)
destination.chmod(0o444)
source.unlink(missing_ok=True)
PY
}

write_release_inputs() {
  local release_dir="$1"
  local manifest="$release_dir/release-manifest.json"
  local secrets_dir="$release_dir/secrets"
  local env_file="$release_dir/compose.env"
  mkdir -m 700 "$secrets_dir"

  fetch_secret_to_file postgres-password "$secrets_dir/postgres_password"
  fetch_secret_to_file redis-password "$secrets_dir/redis_password"
  fetch_secret_to_file temporal-db-password "$secrets_dir/temporal_db_password"
  fetch_secret_to_file supabase-service-role-key "$secrets_dir/supabase_service_role_key"
  # Task 18: the restricted motionly_worker JWT, mounted only into workers.
  fetch_secret_to_file supabase-worker-key "$secrets_dir/supabase_worker_key"
  fetch_secret_to_file supabase-jwt-secret "$secrets_dir/supabase_jwt_secret"
  fetch_secret_to_file supabase-anon-key "$secrets_dir/supabase_anon_key"
  # Realtime (Task 25): a 16-byte row-encryption key and a Phoenix secret base.
  fetch_secret_to_file realtime-db-enc-key "$secrets_dir/realtime_db_enc_key"
  fetch_secret_to_file realtime-secret-key-base "$secrets_dir/realtime_secret_key_base"
  fetch_secret_to_file realtime-metrics-jwt-secret "$secrets_dir/realtime_metrics_jwt_secret"
  fetch_secret_to_file admin-api-token "$secrets_dir/admin_api_token"
  fetch_secret_to_file worker-api-token "$secrets_dir/worker_api_token"
  fetch_secret_to_file metrics-bearer-token "$secrets_dir/metrics_bearer_token"
  # Stock-video providers are application settings consumed by the API. Keep
  # them in protected release inputs and inject them only into Compose's API
  # environment; values never enter Git or command arguments.
  fetch_secret_to_file pexels-api-key "$secrets_dir/pexels_api_key"
  fetch_secret_to_file pixabay-api-key "$secrets_dir/pixabay_api_key"
  # Inference routes (plan Task 26): one declarative registry and one generic
  # secrets bundle replace the old per-vendor keys. The registry's
  # ``bundle:<key>`` refs read keys out of this JSON object; no vendor-named
  # secret parameter is fetched. A missing parameter leaves an empty file so a
  # registry that uses only ``file:`` refs (or no secrets) still deploys.
  fetch_secret_to_file ai-secrets-bundle "$secrets_dir/ai_secrets_bundle" optional
  # Bedrock (docs/aws): the dedicated inference IAM user's static access keys,
  # mounted only into the API and the workers that call a Bedrock route. Fetched
  # with ``optional``, like the bundle above, so a deployment without any
  # Bedrock route leaves the two files empty and still starts.
  fetch_secret_to_file aws-inference-access-key-id "$secrets_dir/aws_inference_access_key_id" optional
  fetch_secret_to_file aws-inference-secret-access-key "$secrets_dir/aws_inference_secret_access_key" optional
  # Origin guard (infra/aws/edge-contract.json): CloudFront sends the current
  # value; the previous one exists only while a rotation deploys.
  fetch_secret_to_file edge/origin-verify "$secrets_dir/edge_origin_verify"
  fetch_secret_to_file edge/origin-verify-previous "$secrets_dir/edge_origin_verify_previous" optional
  # Rehearsal only (refused above otherwise): files the CPU-stage overlay
  # mounts, such as the CloudFront simulator's test certificate.
  for name in ${MOTIONLY_REHEARSAL_EXTRA_SECRETS:-}; do
    fetch_base64_secret_to_file "$name" "$secrets_dir/${name//-/_}"
  done
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
target.chmod(0o444)
PY

  # Compose interpolation receives only non-secret release topology and image
  # metadata, plus the values upstream images can read only from environment.
  python3 - "$manifest" "$env_file" "$secrets_dir" "${MOTIONLY_APP_DOMAIN:?}" "$MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE" "$MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL" "${MOTIONLY_BILLING_MODE:?}" <<'PY'
import json
import os
import sys
from pathlib import Path
manifest_path, env_path, secrets_dir, app_domain, temporal_admin_tools_image, bootstrap_admin_until, billing_mode = sys.argv[1:]
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
    # (No database password: Auth, REST and Storage read their own from
    # per-service credentials volumes; database audit DB-21.)
    "REDIS_PASSWORD": secret("redis_password"),
    "SUPABASE_JWT_SECRET": secret("supabase_jwt_secret"),
    "SUPABASE_ANON_KEY": secret("supabase_anon_key"),
    "SUPABASE_SERVICE_ROLE_KEY": secret("supabase_service_role_key"),
    # One public origin (the CloudFront domain): /api and /supabase on it.
    "APP_DOMAIN": app_domain,
    "PUBLIC_ORIGIN": f"https://{app_domain}",
    "BILLING_MODE": billing_mode,
    "STOCK_VIDEO_PROVIDER": "pexels_pixabay",
    "PEXELS_API_KEY": secret("pexels_api_key"),
    "PIXABAY_API_KEY": secret("pixabay_api_key"),
}
for name, value in environment.items():
    if "\n" in value or "\r" in value:
        raise SystemExit(f"unsafe newline in {name}")
with open(env_path, "w", encoding="utf-8") as output:
    for name, value in environment.items():
        output.write(f"{name}={value}\n")
os.chmod(env_path, 0o600)
PY
}

login_registry() {
  # Only the rehearsal's loopback registry runs without credentials (checked above).
  [[ -n "${MOTIONLY_REGISTRY_USERNAME:-}" ]] || return 0
  aws ssm get-parameter --with-decryption --name "$ssm_prefix/registry-read-token" \
    --query Parameter.Value --output text \
    | docker login "$MOTIONLY_REGISTRY" --username "$MOTIONLY_REGISTRY_USERNAME" --password-stdin >/dev/null
}

compose_for() {
  local release_dir="$1"
  shift
  local -a files=(-f "$release_dir/bundle/infra/docker-compose.gpu.yml")
  local -a profiles=()
  # ComputeProfile=cpu (infra/aws) has no GPU, so the GPU services stay off.
  local topology="base" stems="disabled" profile="$MOTIONLY_COMPUTE_PROFILE"
  # A release applied by this agent records its resolved topology next to
  # compose.env (write_release_topology). Read it WITHOUT `source`: a strict
  # line parser accepts only the three expected keys and their allowed values,
  # so a tampered or unexpected file fails closed rather than running shell.
  if [[ -f "$release_dir/topology.env" ]]; then
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      key="${line%%=*}"
      val="${line#*=}"
      case "$key=$val" in
        MOTIONLY_COMPUTE_PROFILE=gpu | MOTIONLY_COMPUTE_PROFILE=cpu) profile="$val" ;;
        MOTIONLY_CPU_TOPOLOGY=base | MOTIONLY_CPU_TOPOLOGY=gpu-free) topology="$val" ;;
        MOTIONLY_LOCAL_STEMS=enabled | MOTIONLY_LOCAL_STEMS=disabled) stems="$val" ;;
        *)
          echo "unrecognised entry in $release_dir/topology.env: $line" >&2
          return 1
          ;;
      esac
    done <"$release_dir/topology.env"
    if [[ "$profile" != "$MOTIONLY_COMPUTE_PROFILE" ]]; then
      echo "recorded topology profile ($profile) does not match host MOTIONLY_COMPUTE_PROFILE ($MOTIONLY_COMPUTE_PROFILE)" >&2
      return 1
    fi
  fi
  # Files: gpu.yml, then the bundle's GPU-free overlay when gpu-free, then the
  # rehearsal overlay (refused outside rehearsal above) last.
  [[ "$topology" != gpu-free ]] || files+=(-f "$release_dir/bundle/infra/docker-compose.remote-ai.yml")
  [[ -z "${MOTIONLY_COMPOSE_OVERLAY:-}" ]] || files+=(-f "$MOTIONLY_COMPOSE_OVERLAY")
  [[ "$MOTIONLY_COMPUTE_PROFILE" != gpu ]] || profiles=(--profile gpu)
  # gpu-free with local stems starts the Demucs worker (--profile local-stems).
  if [[ "$topology" == gpu-free && "$stems" == enabled ]]; then
    profiles+=(--profile local-stems)
  fi
  docker compose --project-name motionly "${profiles[@]}" --env-file "$release_dir/compose.env" "${files[@]}" "$@"
}

validate_temporal_canary_gate() {
  local release_dir="$1"
  "$MOTIONLY_AGENT_ROOT/bin/temporal-canary-gate" \
    --release-dir "$release_dir" \
    --overlap-proof "$MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE"
}

# Releases from before database audit DB-21 log Auth, REST and Storage in
# with the postgres password itself; later releases give those three roles
# their own passwords (infra/db/apply-role-passwords.sh, the db-roles
# service). Before such an older release starts again (an operator rollback,
# or restoring it after a failed upgrade), set the roles back to the postgres
# password so its services can connect. The password goes from the release's
# protected secret file to psql's stdin; postgres trusts its own loopback.
restore_shared_role_passwords() {
  local release_dir="$1"
  local compose_file="$release_dir/bundle/infra/docker-compose.gpu.yml"
  if [[ ! -f "$compose_file" ]] || grep -q '^  db-roles:' "$compose_file"; then
    return 0
  fi
  compose_for "$release_dir" up -d --wait --wait-timeout 300 postgres || return 1
  python3 - "$release_dir/secrets/postgres_password" <<'PY' \
    | compose_for "$release_dir" exec -T postgres \
      psql -X --no-psqlrc -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -U supabase_admin -d postgres \
    || return 1
from pathlib import Path
import sys
password = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not password or "\n" in password or "\r" in password:
    raise SystemExit("postgres password is invalid")
literal = "'" + password.replace("'", "''") + "'"
print("set log_statement = 'none';")
print("set log_min_duration_statement = -1;")
for role in ("authenticator", "supabase_auth_admin", "supabase_storage_admin"):
    print(f"alter role {role} with password {literal};")
PY
  echo "Restored the shared database role passwords this older release expects" >&2
}

# Called as `if ! activate_release ...`, where bash ignores `set -e`; every
# step therefore fails the function explicitly.
activate_release() {
  local release_dir="$1"
  # GPU workers prefetch model weights before they report healthy.
  local wait_timeout=600
  [[ "$MOTIONLY_COMPUTE_PROFILE" != gpu ]] || wait_timeout=1200
  compose_for "$release_dir" config -q || return 1
  compose_for "$release_dir" pull --quiet || return 1
  restore_shared_role_passwords "$release_dir" || return 1
  compose_for "$release_dir" up -d --wait --wait-timeout "$wait_timeout" --remove-orphans || return 1
  compose_for "$release_dir" exec -T api \
    python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8000/health/live').status == 200" \
    || return 1
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

# After a failed activation: bring back the release that was running, which
# is still `current` (it changes only after a release becomes healthy).
restore_current_release() {
  [[ -L "$current_link" ]] || {
    echo "No current release is recorded to restore" >&2
    exit 66
  }
  local current_dir
  current_dir="$(readlink -f "$current_link")"
  activate_release "$current_dir"
  echo "Restored the running release $(basename "$current_dir")" >&2
}

# Operator rollback (--rollback): go back to the release before `current`.
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
  login_registry
  rollback_previous_release
  exit 0
fi

release_dir="$(fetch_release)"
if [[ "$mode" == "preflight" ]]; then
  validate_release_topology "$release_dir"
  validate_temporal_canary_gate "$release_dir"
  echo "Validated signed release $release_id (topology $MOTIONLY_CPU_TOPOLOGY, local stems $MOTIONLY_LOCAL_STEMS) and Temporal canary evidence; no containers or secrets were changed."
  exit 0
fi

validate_release_topology "$release_dir"
validate_temporal_canary_gate "$release_dir"
write_release_inputs "$release_dir"
write_release_topology "$release_dir"
login_registry
if ! activate_release "$release_dir"; then
  echo "Release $release_id failed health verification; restoring the running release." >&2
  restore_current_release
  exit 70
fi
record_current_release "$release_dir"
echo "Activated immutable release $release_id" >&2
