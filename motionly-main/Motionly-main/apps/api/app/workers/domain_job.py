import argparse
import json
import sys
from types import SimpleNamespace
from uuid import UUID

from app.services.queue import EnqueuedJob


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
    from app.api.internal_jobs import _execute_job
    from app.main import app

    job = EnqueuedJob(
        id=str(payload["job_id"]),
        kind=str(payload["kind"]),
        queue_name=str(payload["queue_name"]),
        workspace_id=UUID(str(payload["workspace_id"])),
        project_id=UUID(str(payload["project_id"])),
        resource_id=UUID(str(payload["resource_id"])),
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
    )
    return _execute_job(job=job, request=SimpleNamespace(app=app))


def _serve() -> None:
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            if request.get("control") == "ping":
                response = {"id": request.get("id"), "result": {"ok": True}}
                print(json.dumps(response, default=str), flush=True)
                continue
            result = _execute(dict(request.get("payload") or {}))
            response = {"id": request.get("id"), "result": result}
        except Exception as exc:
            from app.core.logging import redact_string
            response = {"id": request.get("id"), "error": redact_string(str(exc))}
        print(json.dumps(response, default=str), flush=True)


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
