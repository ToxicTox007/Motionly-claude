"""PaymentGateway interface + the mock gateway client.

Billing talks to a payment gateway only through the :class:`PaymentGateway`
protocol. The one concrete implementation here, :class:`DemoGatewayClient`,
speaks server-to-server HTTP to the Task 22 demo gateway using a merchant API
key (a shared secret), and never moves money. A :class:`NullGateway` is used
when ``BILLING_MODE=disabled`` so nothing ever calls out.

Order/subscription shapes mirror app.mock_gateway.models but are kept as plain
dicts here so the API layer does not depend on the gateway package internals
beyond importing the webhook models/signing for the receiver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from app.api.errors import ApiError

DEMO_NOTICE = "Demo payment — no money is charged"


@dataclass(frozen=True)
class GatewayOrder:
    id: str
    status: str
    amount_paise: int
    currency: str
    checkout_url: str


@dataclass(frozen=True)
class GatewaySubscription:
    id: str
    status: str
    amount_paise: int
    currency: str


@runtime_checkable
class PaymentGateway(Protocol):
    """The contract billing depends on. Implementations never move real money."""

    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str | None,
        notes: dict[str, str],
    ) -> GatewayOrder: ...

    def checkout_url_for(self, order_id: str) -> str: ...

    def refund_order(self, *, order_id: str, amount_paise: int | None) -> dict[str, Any]: ...

    def cancel_subscription(self, *, subscription_id: str) -> dict[str, Any]: ...


class NullGateway:
    """Used when BILLING_MODE=disabled: every call is a hard error (B12)."""

    def _disabled(self) -> ApiError:
        return ApiError(503, "billing_disabled", "Billing is disabled.")

    def create_order(self, **_: Any) -> GatewayOrder:
        raise self._disabled()

    def checkout_url_for(self, order_id: str) -> str:
        raise self._disabled()

    def refund_order(self, **_: Any) -> dict[str, Any]:
        raise self._disabled()

    def cancel_subscription(self, **_: Any) -> dict[str, Any]:
        raise self._disabled()


class DemoGatewayClient:
    """Server-to-server client for the Task 22 demo gateway.

    Authenticates with a merchant API key (Bearer). ``base_url`` is the internal
    gateway URL (e.g. http://payment-gateway:8010); ``public_base_url`` is the
    browser-facing origin used to build the checkout page link.
    """

    def __init__(
        self,
        *,
        base_url: str,
        merchant_api_key: str,
        public_base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.public_base_url = (public_base_url or base_url).rstrip("/")
        self.merchant_api_key = merchant_api_key
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.merchant_api_key}"},
            transport=self._transport,
            timeout=self._timeout,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        code = "gateway_request_failed"
        message = "Payment gateway request failed."
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = error.get("code") or code
                message = error.get("message") or message
            elif isinstance(body, dict) and body.get("detail"):
                message = str(body["detail"])
        except ValueError:
            pass
        raise ApiError(502, code, message)

    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str | None,
        notes: dict[str, str],
    ) -> GatewayOrder:
        with self._client() as client:
            response = client.post(
                "/v1/orders",
                json={
                    "amount_paise": amount_paise,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": notes,
                },
            )
        self._raise_for_status(response)
        body = response.json()
        return GatewayOrder(
            id=str(body["id"]),
            status=str(body["status"]),
            amount_paise=int(body["amount_paise"]),
            currency=str(body["currency"]),
            checkout_url=self.checkout_url_for(str(body["id"])),
        )

    def checkout_url_for(self, order_id: str) -> str:
        return f"{self.public_base_url}/pay/checkout/{order_id}"

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(f"/v1/orders/{order_id}")
        self._raise_for_status(response)
        return response.json()

    def refund_order(self, *, order_id: str, amount_paise: int | None) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(
                f"/v1/orders/{order_id}/refund",
                json={"amount_paise": amount_paise} if amount_paise is not None else {},
            )
        self._raise_for_status(response)
        return response.json()

    def cancel_subscription(self, *, subscription_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(f"/v1/subscriptions/{subscription_id}/cancel")
        self._raise_for_status(response)
        return response.json()


def build_gateway(settings: object) -> PaymentGateway:
    """Factory: NullGateway when billing is disabled, else the mock client."""
    billing_mode = str(getattr(settings, "billing_mode", "") or "").strip().lower()
    if billing_mode != "demo":
        return NullGateway()
    base_url = str(getattr(settings, "mock_gateway_base_url", "") or "")
    merchant_key = getattr(settings, "merchant_api_key", None)
    if not base_url or not merchant_key:
        return NullGateway()
    return DemoGatewayClient(
        base_url=base_url,
        merchant_api_key=str(merchant_key),
        public_base_url=str(getattr(settings, "mock_gateway_public_base_url", "") or "") or None,
    )
