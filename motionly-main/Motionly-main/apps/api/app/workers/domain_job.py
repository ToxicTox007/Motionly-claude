import argparse
import json
import sys
from types import SimpleNamespace
from uuid import UUID

from app.services.queue import EnqueuedJob
from app.workers.executor_protocol import error_reply, open_protocol_channel


def main() -> None:
    if "--serve" in sys.argv:
        _serve()
        return
    args = _arguments()
    result = _execute(vars(args))
    print(json.dumps(result, default=str))
    if result.get("failed"):
        raise SystemExit(1)


def _execute(payload: dict[str, object]) -> dict[str, object]:
    from app.main import app
    from app.services.job_engine import _execute_job
    from app.services.tracing import current_trace_id

    resource_id = payload.get("resource_id")
    job = EnqueuedJob(
        id=str(payload["job_id"]),
        kind=str(payload["kind"]),
        queue_name=str(payload["queue_name"]),
        # BullMQ transport attempts already made: per-kind retry caps (the paid
        # edit-planner limit) depend on it (audit WJ-12).
        attempts=max(0, int(payload.get("attempts_made") or 0)),
        workspace_id=UUID(str(payload["workspace_id"])),
        project_id=UUID(str(payload["project_id"])),
        # effects_config without an edit plan carries no resource id.
        resource_id=UUID(str(resource_id)) if resource_id not in (None, "", "None") else None,
        workflow_run_id=(
            UUID(str(payload["workflow_run_id"])) if payload.get("workflow_run_id") else None
        ),
        workflow_step_id=(
            UUID(str(payload["workflow_step_id"])) if payload.get("workflow_step_id") else None
        ),
        step_key=str(payload["step_key"]) if payload.get("step_key") else None,
        step_kind=str(payload["step_kind"]) if payload.get("step_kind") else None,
        attempt_key=str(payload["attempt_key"]) if payload.get("attempt_key") else None,
        workflow_attempt_number=(
            int(payload["workflow_attempt_number"])
            if payload.get("workflow_attempt_number") is not None
            else None
        ),
        workflow_max_attempts=(
            int(payload["workflow_max_attempts"])
            if payload.get("workflow_max_attempts") is not None
            else None
        ),
        input_hash=str(payload["input_hash"]) if payload.get("input_hash") else None,
        next_run_state=(
            str(payload["next_run_state"]) if payload.get("next_run_state") else None
        ),
        render_engine=str(payload.get("render_engine") or "ffmpeg_basic"),
        registry_fingerprint=(
            str(payload["registry_fingerprint"]) if payload.get("registry_fingerprint") else None
        ),
        trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
        render_attempt_id=(
            str(payload["render_attempt_id"]) if payload.get("render_attempt_id") else None
        ),
    )
    token = current_trace_id.set(job.trace_id)
    try:
        return _execute_job(job=job, request=SimpleNamespace(app=app))
    finally:
        current_trace_id.reset(token)


def _serve() -> None:
    # Before anything else runs: only `channel` may write to the real stdout.
    channel = open_protocol_channel()
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            if request.get("control") == "ping":
                channel.send({"id": request.get("id"), "result": {"ok": True}})
                continue
            result = _execute(dict(request.get("payload") or {}))
            response = {"id": request.get("id"), "result": result}
        except Exception as exc:
            response = error_reply(request.get("id"), exc)
        channel.send(response)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute one BullMQ domain job directly in a media worker process."
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--queue-name", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--workflow-step-id")
    parser.add_argument("--step-key")
    parser.add_argument("--step-kind")
    parser.add_argument("--attempt-key")
    parser.add_argument("--workflow-attempt-number", type=int)
    parser.add_argument("--workflow-max-attempts", type=int)
    parser.add_argument("--input-hash")
    parser.add_argument("--next-run-state")
    parser.add_argument("--render-engine", default="ffmpeg_basic")
    return parser.parse_args()


if __name__ == "__main__":
    main()
