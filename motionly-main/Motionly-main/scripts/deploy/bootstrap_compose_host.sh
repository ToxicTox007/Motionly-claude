#!/usr/bin/env bash
# Bootstrap the one supported production host: a single Azure VM running Compose.
# The VM must have a system-assigned managed identity before this agent is installed.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root so the agent and host configuration are protected." >&2
  exit 77
fi

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
agent_root="/opt/motionly/agent"
host_config="/etc/motionly/host.env"

for command in docker az cosign python3 install; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required before host bootstrap" >&2
    exit 127
  }
done

docker compose version >/dev/null
if [[ ! -f "$host_config" ]]; then
  install -D -m 600 "$source_root/infra/compose-host/host.env.example" "$host_config"
  echo "Created $host_config. Fill non-secret Azure resource names, then rerun bootstrap." >&2
  exit 78
fi

# shellcheck disable=SC1090
source "$host_config"
for value in MOTIONLY_KEY_VAULT_NAME MOTIONLY_RELEASE_STORAGE_ACCOUNT MOTIONLY_GHCR_USERNAME MOTIONLY_APP_DOMAIN MOTIONLY_API_DOMAIN MOTIONLY_SUPABASE_DOMAIN; do
  [[ -n "${!value:-}" ]] || {
    echo "$value must be set in $host_config" >&2
    exit 78
  }
done

install -d -m 700 "$agent_root/bin" /var/lib/motionly/releases /var/lib/motionly/manifests
install -m 755 "$source_root/scripts/deploy/apply_release.sh" "$agent_root/bin/apply-release"
install -m 755 "$source_root/scripts/release/generate_release_manifest.py" "$agent_root/bin/generate-release-manifest"
install -m 755 "$source_root/scripts/release/package_release_bundle.py" "$agent_root/bin/package-release-bundle"
install -m 755 "$source_root/scripts/deploy/temporal_canary_gate.sh" "$agent_root/bin/temporal-canary-gate"
install -m 644 "$source_root/infra/release-manifest.schema.json" "$agent_root/release-manifest.schema.json"

cat > /etc/systemd/system/motionly-release-agent.service <<'UNIT'
[Unit]
Description=Motionly Compose release agent
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=/etc/motionly/host.env
ExecStart=/bin/true
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/motionly /run/docker.sock

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
printf 'Motionly single-VM release agent installed at %s/bin/apply-release.\n' "$agent_root"
