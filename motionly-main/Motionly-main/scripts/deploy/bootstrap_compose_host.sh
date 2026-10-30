#!/usr/bin/env bash
# Install the release agent on the one supported production host: the EC2
# instance of the infra/aws stack, whose instance role reads releases from S3
# and secrets from SSM Parameter Store. Run once as root from a checkout of the
# release tag (for example over SSM Session Manager; the instance has no SSH).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root so the agent and host configuration are protected." >&2
  exit 77
fi

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
agent_root="/opt/motionly/agent"
host_config="/etc/motionly/host.env"

for command in docker aws cosign python3 install; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required before host bootstrap" >&2
    exit 127
  }
done

docker compose version >/dev/null
if [[ ! -f "$host_config" ]]; then
  install -D -m 600 "$source_root/infra/compose-host/host.env.example" "$host_config"
  echo "Created $host_config. Fill the non-secret stack outputs and settings, then rerun bootstrap." >&2
  exit 78
fi

# shellcheck disable=SC1090
source "$host_config"
for value in MOTIONLY_ENVIRONMENT MOTIONLY_RELEASE_BUCKET MOTIONLY_BACKUP_BUCKET MOTIONLY_COMPUTE_PROFILE MOTIONLY_REGISTRY MOTIONLY_REGISTRY_USERNAME MOTIONLY_APP_DOMAIN; do
  [[ -n "${!value:-}" ]] || {
    echo "$value must be set in $host_config" >&2
    exit 78
  }
done

install -d -m 700 "$agent_root/bin" "${MOTIONLY_RELEASE_ROOT:?}"
install -m 755 "$source_root/scripts/deploy/apply_release.sh" "$agent_root/bin/apply-release"
install -m 755 "$source_root/scripts/release/generate_release_manifest.py" "$agent_root/bin/generate-release-manifest"
install -m 755 "$source_root/scripts/deploy/temporal_canary_gate.sh" "$agent_root/bin/temporal-canary-gate"
install -m 755 "$source_root/scripts/deploy/db_backup.sh" "$agent_root/bin/db-backup"
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
ReadWritePaths=/srv/motionly /run/docker.sock

[Install]
WantedBy=multi-user.target
UNIT

# Database backups (database audit DB-02): a daily WAL-G base backup and a
# 5-minute copy of the WAL archive to the BackupsBucket, with CloudWatch
# metrics the stack's alarms watch. See docs/ops/database-operations.md.
cat > /etc/systemd/system/motionly-db-backup@.service <<'UNIT'
[Unit]
Description=Motionly database backup (%i)
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=/etc/motionly/host.env
ExecStart=/opt/motionly/agent/bin/db-backup %i
StateDirectory=motionly-backup
Environment=MOTIONLY_BACKUP_STATE_DIR=/var/lib/motionly-backup
NoNewPrivileges=true
PrivateTmp=true
TimeoutStartSec=6h
UNIT

cat > /etc/systemd/system/motionly-db-backup-daily.timer <<'UNIT'
[Unit]
Description=Daily Motionly database base backup

[Timer]
OnCalendar=*-*-* 02:30:00 UTC
Persistent=true
RandomizedDelaySec=10min
Unit=motionly-db-backup@daily.service

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/motionly-db-backup-frequent.timer <<'UNIT'
[Unit]
Description=Copy the Motionly WAL archive to S3 every five minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Unit=motionly-db-backup@frequent.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now motionly-db-backup-daily.timer motionly-db-backup-frequent.timer
printf 'Motionly release agent installed at %s/bin/apply-release.\n' "$agent_root"
