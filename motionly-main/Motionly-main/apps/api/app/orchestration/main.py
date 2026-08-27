from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

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
    asyncio.run(run_role(args.role, settings))


if __name__ == "__main__":
    main()
