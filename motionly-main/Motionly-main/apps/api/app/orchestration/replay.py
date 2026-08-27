from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, WorkflowReplayResult

from app.orchestration.history import validate_history_document, validate_history_file
from app.orchestration.workflow import MotionlyWorkflow


async def replay_history_document(
    document: dict[str, Any],
    *,
    workflow_id: str,
    replayer: Replayer | None = None,
) -> WorkflowReplayResult:
    if not workflow_id or len(workflow_id) > 240:
        raise ValueError("workflow_id is required and must be bounded")
    validate_history_document(document)
    history = WorkflowHistory.from_json(workflow_id, document)
    runner = replayer or Replayer(workflows=[MotionlyWorkflow])
    return await runner.replay_workflow(history)


async def replay_history_file(
    path: str | Path,
    *,
    workflow_id: str | None = None,
    replayer: Replayer | None = None,
) -> WorkflowReplayResult:
    history_path = Path(path)
    document = validate_history_file(history_path)
    resolved_id = workflow_id or _document_workflow_id(document) or history_path.stem
    return await replay_history_document(
        document,
        workflow_id=resolved_id,
        replayer=replayer,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and replay a Motionly Temporal workflow history."
    )
    parser.add_argument("history", type=Path)
    parser.add_argument("--workflow-id")
    args = parser.parse_args()
    result = asyncio.run(replay_history_file(args.history, workflow_id=args.workflow_id))
    print(
        json.dumps(
            {"replay_failure": str(result.replay_failure) if result.replay_failure else None}
        )
    )


def _document_workflow_id(document: dict[str, Any]) -> str | None:
    value = document.get("workflowId") or document.get("workflow_id")
    return value if isinstance(value, str) and value else None


if __name__ == "__main__":
    main()
