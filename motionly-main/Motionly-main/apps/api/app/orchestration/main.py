from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.production import validate_production_settings
from app.orchestration.dispatcher import run_dispatcher
from app.orchestration.reconciler import run_reconciler
from app.orchestration.worker import run_worker

ALLOWED_ROLES = (
    "temporal-dispatcher",
    "temporal-worker",
    "workflow-reconciler",
)

# Refreshed while a role runs; the Compose health check fails once it goes
# stale, so a release whose orchestration role hangs never reports healthy.
ORCHESTRATION_LIVENESS_FILE = Path("/tmp/motionly-orchestration-alive")
LIVENESS_INTERVAL_SECONDS = 15.0


async def run_with_liveness(
    role: Coroutine[Any, Any, None],
    file: Path = ORCHESTRATION_LIVENESS_FILE,
    *,
    interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
) -> None:
    async def refresh() -> None:
        while True:
            file.touch()
            await asyncio.sleep(interval_seconds)

    refresher = asyncio.create_task(refresh())
    try:
        await role
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher


async def run_role(role: str, settings: Settings) -> None:
    if role == "temporal-dispatcher":
        await run_dispatcher(settings)
        return
    if role == "temporal-worker":
        await run_worker(settings)
        return
    if role == "workflow-reconciler":
        await run_reconciler(settings)
        return
    raise ValueError("orchestration role is not allowlisted")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a Motionly orchestration role.")
    parser.add_argument("role", choices=ALLOWED_ROLES)
    args = parser.parse_args(argv)

    settings = get_settings()
    validate_production_settings(settings)
    configured_role = settings.process_role.strip().lower()
    if configured_role != args.role:
        raise RuntimeError(
            f"PROCESS_ROLE {configured_role!r} does not match requested role {args.role!r}"
        )
    asyncio.run(run_with_liveness(run_role(args.role, settings)))


if __name__ == "__main__":
    main()
