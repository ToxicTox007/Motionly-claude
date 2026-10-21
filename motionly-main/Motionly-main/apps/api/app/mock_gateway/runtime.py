"""Default webhook transport + renewal scheduler for the mock gateway."""

from __future__ import annotations

import asyncio
import logging

import httpx

from .engine import PaymentEngine
from .webhooks import DeliveryResult

logger = logging.getLogger("mock_gateway.runtime")


class HttpxWebhookTransport:
    """Delivers webhooks over real HTTP using httpx."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    async def post(self, url: str, *, headers: dict[str, str], body: bytes) -> DeliveryResult:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, content=body)
            return DeliveryResult(status_code=response.status_code)


class RenewalScheduler:
    """Runs subscription renewals on a configurable period (short in tests)."""

    def __init__(self, engine: PaymentEngine, period_seconds: float) -> None:
        self._engine = engine
        self._period = period_seconds
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._period)
            except TimeoutError:
                await self.run_once()

    async def run_once(self) -> int:
        charged = 0
        for row in self._engine.store.active_subscriptions():
            try:
                if await self._engine.run_renewal(row):
                    charged += 1
            except Exception:  # noqa: BLE001 - one bad renewal must not stop the loop
                logger.exception("renewal failed for subscription %s", row.id)
        return charged

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            await self._task
            self._task = None
