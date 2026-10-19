"""Orchestration roles refresh a liveness file for the Compose health check."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.orchestration.main import ORCHESTRATION_LIVENESS_FILE, run_with_liveness


def test_the_liveness_file_is_refreshed_while_the_role_runs(tmp_path: Path) -> None:
    file = tmp_path / "alive"
    seen: list[float] = []

    async def role() -> None:
        for _ in range(3):
            await asyncio.sleep(0.03)
            seen.append(file.stat().st_mtime)

    asyncio.run(run_with_liveness(role(), file, interval_seconds=0.01))

    assert len(seen) == 3 and seen[-1] > seen[0]
    assert time.time() - file.stat().st_mtime < 5


def test_the_role_error_propagates_and_refreshing_stops(tmp_path: Path) -> None:
    file = tmp_path / "alive"

    async def role() -> None:
        await asyncio.sleep(0.02)
        raise RuntimeError("role crashed")

    try:
        asyncio.run(run_with_liveness(role(), file, interval_seconds=0.01))
    except RuntimeError as error:
        assert str(error) == "role crashed"
    else:
        raise AssertionError("the role error must propagate")
    stopped = file.stat().st_mtime
    time.sleep(0.05)
    assert file.stat().st_mtime == stopped


def test_the_default_file_is_the_one_the_compose_health_checks_read() -> None:
    assert str(ORCHESTRATION_LIVENESS_FILE) == "/tmp/motionly-orchestration-alive"
