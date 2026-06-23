#!/usr/bin/env bash
set -euo pipefail

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required for backup restore verification" >&2
  exit 127
fi
if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore is required for backup restore verification" >&2
  exit 127
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "psql is required for backup restore verification" >&2
  exit 127
fi

source_url="${1:?source database URL is required}"
restore_url="${2:-$source_url}"
work_dir="${TMPDIR:-/tmp}/motionly-backup-restore"
mkdir -p "$work_dir"
dump_file="$work_dir/motionly-prelaunch.dump"

pg_dump --format=custom --no-owner --no-acl --file "$dump_file" "$source_url"
pg_restore --clean --if-exists --no-owner --dbname "$restore_url" "$dump_file"
psql "$restore_url" --quiet --tuples-only --command "select count(*) from information_schema.tables where table_schema = 'public';" | grep -Eq '[1-9][0-9]*'
