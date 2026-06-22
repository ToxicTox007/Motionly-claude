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
        render_engine=str(payload.get("render_engine") or "ffmpeg_basic"),
    )
    return _execute_job(job=job, request=SimpleNamespace(app=app))


def _serve() -> None:
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            result = _execute(dict(request.get("payload") or {}))
            response = {"id": request.get("id"), "result": result}
        except Exception as exc:
            response = {"id": request.get("id"), "error": str(exc)}
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
    parser.add_argument("--render-engine", default="ffmpeg_basic")
    return parser.parse_args()


if __name__ == "__main__":
    main()
