from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.production import validate_production_settings
from app.orchestration.dispatcher import run_dispatcher
from app.orchestration.reconciler import run_reconciler
from app.orchestration.worker import run_worker

logger = logging.getLogger(__name__)

ALLOWED_ROLES = (
    "temporal-dispatcher",
    "temporal-worker",
    "workflow-reconciler",
)

# Refreshed while a role runs; the Compose health check fails once it goes
# stale, so a release whose orchestration role hangs never reports healthy.
ORCHESTRATION_LIVENESS_FILE = Path("/tmp/motionly-orchestration-alive")
LIVENESS_INTERVAL_SECONDS = 15.0
# Loop roles (dispatcher, reconciler) report progress after every iteration.
# Without progress for this long the liveness file stops being refreshed, so a
# loop stuck on a hung Supabase/Temporal call is restarted by its health check
# instead of looking healthy forever (audit WJ-20). The reconciler's interval is
# 30 s by default, so 180 s tolerates several slow passes.
PROGRESS_WINDOW_SECONDS = 180.0


class ProgressTracker:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._last = clock()

    def mark(self) -> None:
        self._last = self._clock()

    def recent(self, window_seconds: float) -> bool:
        return self._clock() - self._last < window_seconds


async def run_with_liveness(
    role: Coroutine[Any, Any, None],
    file: Path = ORCHESTRATION_LIVENESS_FILE,
    *,
    interval_seconds: float = LIVENESS_INTERVAL_SECONDS,
    progress: ProgressTracker | None = None,
    progress_window_seconds: float = PROGRESS_WINDOW_SECONDS,
) -> None:
    async def refresh() -> None:
        while True:
            if progress is None or progress.recent(progress_window_seconds):
                file.touch()
            else:
                logger.error("orchestration_role_stalled", extra={"file": str(file)})
            await asyncio.sleep(interval_seconds)

    refresher = asyncio.create_task(refresh())
    try:
        await role
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher


async def run_role(role: str, settings: Settings, progress: ProgressTracker | None = None) -> None:
    if role == "temporal-dispatcher":
        await run_dispatcher(settings, progress=progress.mark if progress else None)
        return
    if role == "temporal-worker":
        # The SDK owns the worker's poll loops; liveness is the event loop.
        await run_worker(settings)
        return
    if role == "workflow-reconciler":
        await run_reconciler(settings, progress=progress.mark if progress else None)
        return
    raise ValueError("orchestration role is not allowlisted")


async def run_until_signalled(
    work: Coroutine[Any, Any, None],
    *,
    signals: Sequence[int] = (signal.SIGTERM, signal.SIGINT),
) -> bool:
    """Run ``work``; SIGTERM/SIGINT cancel it so it can shut down gracefully.

    These roles run as PID 1 in their containers, where a signal with the
    default disposition is ignored: without a handler, ``docker stop`` waited
    out its grace period and SIGKILLed the process, so the Temporal worker's
    graceful shutdown and the dispatcher's lease release never ran (audit
    WJ-07). Returns True when the work stopped because of a signal.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(work)
    received: list[int] = []

    def stop(signum: int) -> None:
        if not received:
            logger.info("orchestration_shutdown_requested", extra={"signal": signum})
        received.append(signum)
        task.cancel()

    installed = []
    for signum in signals:
        try:
            loop.add_signal_handler(signum, stop, signum)
            installed.append(signum)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    try:
        await task
    except asyncio.CancelledError:
        if not received:
            raise
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
    return bool(received)


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
    progress = ProgressTracker() if args.role != "temporal-worker" else None
    asyncio.run(
        run_until_signalled(
            run_with_liveness(run_role(args.role, settings, progress), progress=progress)
        )
    )


if __name__ == "__main__":
    main()
