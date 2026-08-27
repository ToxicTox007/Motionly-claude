# Verify application and Temporal logical restores using protected libpq service files.
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "Usage: $0 <application-source-service> <application-restore-service> <temporal-source-service> <temporal-restore-service>" >&2
  exit 64
fi
if [[ "${MOTIONLY_TEMPORAL_BACKUP_RESTORE_ACK:-}" != "restore-target" ]]; then
  echo "Set MOTIONLY_TEMPORAL_BACKUP_RESTORE_ACK=restore-target to confirm both restore targets may be cleared." >&2
  exit 64
fi

verifier="$(dirname "$0")/verify_backup_restore.sh"
MOTIONLY_BACKUP_RESTORE_ACK=restore-target "$verifier" "$1" "$2"
MOTIONLY_BACKUP_RESTORE_ACK=restore-target "$verifier" "$3" "$4"
printf 'Application and Temporal database backup/restore verification succeeded.\n'
