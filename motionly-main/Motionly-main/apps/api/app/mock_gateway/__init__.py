"""Mock payment gateway (Task 22).

A standalone FastAPI service that behaves like a real UPI + card payment
gateway for India (prices in INR, amounts in paise) **without ever moving
money**. It is built from the API image but is a separate service with its own
database schema (``mock_gateway``), its own database role, its own webhook
signing secret and its own ``BILLING_MODE`` gate. It is the only ``demo`` kind
stand-in registered in fixtures/registry.json: it may run in production *only*
when ``BILLING_MODE=demo``; it refuses to start otherwise.

Run it with::

    python -m app.mock_gateway
    # or
    uvicorn app.mock_gateway.main:app

------------------------------------------------------------------------------
Webhook payload + signature contract (Task 23 builds the receiver)
------------------------------------------------------------------------------
Every state change (payment captured/failed, subscription created/charged/
cancelled, refund processed) is delivered as an HTTP POST to the configured
``WEBHOOK_URL`` with a JSON body and these headers:

* ``X-Mock-Gateway-Event``   - the event type, e.g. ``payment.captured``
* ``X-Mock-Gateway-Event-Id``- a unique event id (idempotency key for the
  receiver; retries reuse the same id)
* ``X-Mock-Gateway-Timestamp``- unix seconds when the signature was computed
* ``X-Mock-Gateway-Signature``- ``sha256=<hex>`` where ``<hex>`` is
  ``HMAC_SHA256(secret, f"{timestamp}.{raw_body}")``

Receivers MUST (a) reject timestamps outside a tolerance window, (b) recompute
the HMAC over ``f"{timestamp}.{raw_body}"`` and compare in constant time, and
(c) de-duplicate on the event id. The body schema is the exported
:class:`~app.mock_gateway.models.WebhookEnvelope` Pydantic model; import it
rather than re-deriving the shape.

The signature scheme and envelope live in :mod:`app.mock_gateway.signing` and
:mod:`app.mock_gateway.models` so other packages can import them without
importing the FastAPI app.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
