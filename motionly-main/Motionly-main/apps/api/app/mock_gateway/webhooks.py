"""Webhook delivery for the mock payment gateway.

Events are signed with HMAC (see :mod:`app.mock_gateway.signing`), carry a
unique event id, and are retried with exponential backoff. Delivery uses an
injectable transport so tests can assert signatures, retries and duplicate
suppression without real HTTP.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from .models import WebhookEnvelope
from .signing import compute_signature

logger = logging.getLogger("mock_gateway.webhooks")


@dataclass
class DeliveryResult:
    status_code: int


class WebhookTransport(Protocol):
    async def post(
        self, url: str, *, headers: dict[str, str], body: bytes
    ) -> DeliveryResult: ...


def new_event_id() -> str:
    return f"evt_{secrets.token_hex(12)}"


class WebhookDeliverer:
    def __init__(
        self,
        *,
        secret: str,
        url: str | None,
        transport: WebhookTransport,
        max_attempts: int = 5,
        backoff_base_seconds: float = 1.0,
        sleep=asyncio.sleep,
        clock=time.time,
    ) -> None:
        self._secret = secret
        self._url = url
        self._transport = transport
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep
        self._clock = clock
        # event id -> number of successful deliveries, for duplicate suppression
        self._delivered: set[str] = set()

    def sign(self, raw_body: bytes, timestamp: int) -> str:
        return compute_signature(self._secret, timestamp, raw_body)

    async def deliver(self, envelope: WebhookEnvelope) -> bool:
        """Deliver one event with retries. Returns True on success.

        A duplicate (same event id already delivered) is suppressed: it is not
        re-sent. Retries reuse the event id so the receiver can de-duplicate.
        """
        if self._url is None:
            logger.info("no webhook url configured; dropping event %s", envelope.id)
            return False
        if envelope.id in self._delivered:
            logger.info("event %s already delivered; suppressing duplicate", envelope.id)
            return True

        raw_body = envelope.model_dump_json().encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            timestamp = int(self._clock())
            signature = self.sign(raw_body, timestamp)
            headers = {
                "Content-Type": "application/json",
                "X-Mock-Gateway-Event": envelope.event,
                "X-Mock-Gateway-Event-Id": envelope.id,
                "X-Mock-Gateway-Timestamp": str(timestamp),
                "X-Mock-Gateway-Signature": signature,
            }
            try:
                result = await self._transport.post(self._url, headers=headers, body=raw_body)
                if 200 <= result.status_code < 300:
                    self._delivered.add(envelope.id)
                    return True
                last_error = RuntimeError(f"webhook returned {result.status_code}")
            except Exception as error:  # noqa: BLE001 - retried below
                last_error = error
            if attempt < self._max_attempts:
                await self._sleep(self._backoff_base * (2 ** (attempt - 1)))
        logger.warning(
            "webhook delivery failed for event %s after %d attempts: %s",
            envelope.id,
            self._max_attempts,
            last_error,
        )
        return False
