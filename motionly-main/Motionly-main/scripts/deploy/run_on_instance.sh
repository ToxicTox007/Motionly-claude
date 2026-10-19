#!/usr/bin/env bash
# Ask the instance to preflight or apply a release over SSM Run Command, wait
# for it, print its output, and fail unless it succeeded. Only the release id
# (a Git SHA) reaches the remote command line; the instance reads everything
# else itself (scripts/deploy/apply_release.sh).
#
#   scripts/deploy/run_on_instance.sh --instance-id i-0123... --release-id <sha> [--preflight]
set -euo pipefail

instance_id=""
release_id=""
preflight=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --instance-id) instance_id="$2"; shift 2 ;;
    --release-id) release_id="$2"; shift 2 ;;
    --preflight) preflight=" --preflight"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ "$instance_id" =~ ^i-[0-9a-f]{8,17}$ ]] || { echo "invalid instance id" >&2; exit 64; }
[[ "$release_id" =~ ^[0-9a-f]{7,64}$ ]] || { echo "invalid release id" >&2; exit 64; }

remote="/opt/motionly/agent/bin/apply-release --release-id ${release_id}${preflight}"
parameters=$(python3 -c 'import json, sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["3600"]}))' "$remote")
command_id=$(aws ssm send-command \
  --document-name AWS-RunShellScript \
  --instance-ids "$instance_id" \
  --comment "Motionly release ${release_id:0:12}${preflight}" \
  --parameters "$parameters" \
  --query Command.CommandId --output text)
echo "SSM command $command_id on $instance_id: $remote"

status=Pending
for _ in $(seq 1 720); do
  sleep 5
  status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance_id" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in
    Pending | InProgress | Delayed) continue ;;
    *) break ;;
  esac
done
aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance_id" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text || true
if [[ "$status" == "Success" ]]; then
  echo "release command succeeded"
  exit 0
fi
echo "release command finished with status $status" >&2
exit 1
