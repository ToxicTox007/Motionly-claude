#!/usr/bin/env bash
# Database backups on the Compose host (database audit DB-02, DB-15).
#
# Each PostgreSQL container (postgres, temporal-postgres) archives every WAL
# segment with WAL-G into its backup volume (infra/docker-compose.gpu.yml).
# This agent, installed by scripts/deploy/bootstrap_compose_host.sh and run by
# systemd timers as root, completes point-in-time recovery:
#
#   db-backup daily     base backup of each database, local retention, sync, metrics
#   db-backup frequent  sync, metrics (every 5 minutes: bounds data loss to minutes)
#   db-backup base      WAL-G base backup of each database; keep the newest
#                       MOTIONLY_BACKUP_LOCAL_FULL_COPIES (default 2) on the host
#   db-backup sync      copy each backup volume to
#                       s3://$MOTIONLY_BACKUP_BUCKET/database/<environment>/<service>/
#                       (never deletes: the bucket's lifecycle keeps 35 days)
#   db-backup metrics   publish backup, WAL-archive, disk and availability metrics
#                       to CloudWatch (namespace Motionly/Database); the stack's
#                       alarms fire on them
#
# Containers are found by their Compose labels, so the agent follows whichever
# release is active. Restore procedure: docs/ops/database-operations.md.
set -euo pipefail
umask 077

host_env="${MOTIONLY_HOST_ENV:-/etc/motionly/host.env}"
if [[ -f "$host_env" ]]; then
  # shellcheck disable=SC1090
  source "$host_env"
fi
project="${MOTIONLY_COMPOSE_PROJECT:-motionly}"
environment="${MOTIONLY_ENVIRONMENT:?MOTIONLY_ENVIRONMENT must be set (host.env)}"
region="${MOTIONLY_AWS_REGION:-ap-south-1}"
bucket="${MOTIONLY_BACKUP_BUCKET:-}"
local_copies="${MOTIONLY_BACKUP_LOCAL_FULL_COPIES:-2}"
data_mount="${MOTIONLY_DATA_MOUNT:-/srv/motionly}"
state_dir="${MOTIONLY_BACKUP_STATE_DIR:-/var/lib/motionly-backup}"
namespace="Motionly/Database"
services=(postgres temporal-postgres)
declare -A backup_volumes=([postgres]=postgres-backups [temporal-postgres]=temporal-postgres-backups)

[[ "$local_copies" =~ ^[1-9][0-9]*$ ]] || {
  echo "db-backup: MOTIONLY_BACKUP_LOCAL_FULL_COPIES must be a positive integer" >&2
  exit 64
}
install -d -m 700 "$state_dir"
exec 9>"$state_dir/lock"
flock -w 900 9 || {
  echo "db-backup: another run holds $state_dir/lock" >&2
  exit 75
}

container_for() {
  docker ps -q \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$1" | head -n 1
}

base_backups() {
  local svc container failed=0
  for svc in "${services[@]}"; do
    container="$(container_for "$svc")"
    if [[ -z "$container" ]]; then
      echo "db-backup: $svc is not running; no base backup taken" >&2
      failed=1
      continue
    fi
    # supabase_admin over 127.0.0.1 is trusted inside the container (the
    # image's pg_hba); the base backup needs a superuser.
    if ! docker exec -u postgres \
      -e PGHOST=127.0.0.1 -e PGUSER=supabase_admin -e PGDATABASE=postgres \
      "$container" wal-g backup-push /var/lib/postgresql/data; then
      echo "db-backup: base backup of $svc failed" >&2
      failed=1
      continue
    fi
    # Local copies only: the S3 copy keeps every backup for the bucket's
    # lifecycle period. Removing a base backup also removes the WAL only it needed.
    docker exec -u postgres "$container" wal-g delete retain FULL "$local_copies" --confirm \
      || echo "db-backup: local retention for $svc failed (backups kept)" >&2
    echo "db-backup: base backup of $svc complete"
  done
  return "$failed"
}

sync_volumes() {
  if [[ -z "$bucket" ]]; then
    echo "db-backup: MOTIONLY_BACKUP_BUCKET is not set (host.env: the stack's BackupsBucketName output)" >&2
    return 78
  fi
  local svc volume source failed=0
  for svc in "${services[@]}"; do
    volume="${project}_${backup_volumes[$svc]}"
    if ! source="$(docker volume inspect -f '{{.Mountpoint}}' "$volume" 2>/dev/null)"; then
      echo "db-backup: backup volume $volume does not exist" >&2
      failed=1
      continue
    fi
    if aws s3 sync "$source/" "s3://$bucket/database/$environment/$svc/" \
      --region "$region" --only-show-errors --no-progress; then
      date +%s >"$state_dir/$svc.synced"
    else
      echo "db-backup: sync of $svc to S3 failed" >&2
      failed=1
    fi
  done
  return "$failed"
}

publish_metrics() {
  local svc container up archived failed_at latest synced metrics
  metrics="$(mktemp)"
  trap 'rm -f "$metrics"' RETURN
  : >"$metrics.lines"
  for svc in "${services[@]}"; do
    container="$(container_for "$svc")"
    up=0 archived=0 failed_at=0 latest=""
    if [[ -n "$container" ]] && docker exec "$container" pg_isready -q -U postgres -h 127.0.0.1; then
      up=1
      read -r archived failed_at < <(docker exec "$container" psql -U postgres -h 127.0.0.1 -XAt -F ' ' -c \
        "select coalesce(extract(epoch from last_archived_time)::bigint, 0),
                coalesce(extract(epoch from last_failed_time)::bigint, 0)
         from pg_stat_archiver") || true
      latest="$(docker exec -u postgres "$container" wal-g backup-list --json 2>/dev/null || true)"
    fi
    synced="$(cat "$state_dir/$svc.synced" 2>/dev/null || echo 0)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$svc" "$up" "${archived:-0}" "${failed_at:-0}" "$synced" \
      "$(printf '%s' "$latest" | tr -d '\n\t')" >>"$metrics.lines"
  done
  python3 - "$metrics.lines" "$metrics" "$environment" "$(df --output=pcent "$data_mount" | tail -n 1 | tr -dc '0-9')" <<'PY'
import json
import re
import sys
import time
from datetime import datetime, timezone

lines_path, out_path, environment, disk_used = sys.argv[1:]
now = time.time()
data = []

def metric(name, value, unit, service=None):
    dimensions = [{"Name": "Environment", "Value": environment}]
    if service:
        dimensions.append({"Name": "Service", "Value": service})
    data.append({"MetricName": name, "Dimensions": dimensions, "Value": float(value), "Unit": unit})

def backup_time(stamp):
    match = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", stamp)
    if not match:
        raise ValueError(stamp)
    parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc).timestamp()

with open(lines_path, encoding="utf-8") as lines:
    for line in lines:
        service, up, archived, failed_at, synced, backups = line.rstrip("\n").split("\t", 5)
        metric("DatabaseUp", int(up), "Count", service)
        # Failing: the last archive attempt failed after the last success.
        metric("WalArchiveFailing", 1 if int(failed_at) > int(archived) else 0, "Count", service)
        latest = 0.0
        try:
            for backup in json.loads(backups or "[]"):
                latest = max(latest, backup_time(backup["time"]))
        except (ValueError, KeyError, TypeError):
            latest = 0.0
        # No backup yet reads as infinitely old, so the alarm fires.
        metric("BaseBackupAgeSeconds", now - latest if latest else 10**9, "Seconds", service)
        metric("BackupSyncAgeSeconds", now - int(synced) if int(synced) else 10**9, "Seconds", service)
metric("DataVolumeUsedPercent", int(disk_used or 0), "Percent")
with open(out_path, "w", encoding="utf-8") as out:
    json.dump(data, out)
PY
  rm -f "$metrics.lines"
  aws cloudwatch put-metric-data --region "$region" --namespace "$namespace" \
    --metric-data "file://$metrics"
}

command="${1:-}"
case "$command" in
  base) base_backups ;;
  sync) sync_volumes ;;
  metrics) publish_metrics ;;
  daily)
    status=0
    base_backups || status=$?
    sync_volumes || status=$?
    publish_metrics || status=$?
    exit "$status"
    ;;
  frequent)
    status=0
    sync_volumes || status=$?
    publish_metrics || status=$?
    exit "$status"
    ;;
  *)
    echo "usage: $0 daily|frequent|base|sync|metrics" >&2
    exit 64
    ;;
esac
