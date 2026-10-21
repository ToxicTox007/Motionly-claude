"""Configuration for the mock payment gateway.

The gateway reads its *own* environment (it does not import the API's
``app.core.config``): its billing mode, merchant API key, webhook URL + signing
secret, the renewal period (short in tests) and the optional Postgres DSN for
its private ``mock_gateway`` schema. Secrets may be provided inline
(``*_SECRET``) or via a file (``*_SECRET_FILE``) the way the rest of the stack
mounts Docker secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BILLING_MODE_DEMO = "demo"
PRODUCTION_ENVS = {"production"}


class GatewayConfigError(RuntimeError):
    """Raised when the gateway is misconfigured and must not start."""


def _read_secret(
    inline_var: str,
    file_var: str,
    *,
    env: dict[str, str],
    default: str | None = None,
) -> str | None:
    """Prefer an inline secret, then a secret file, else the default."""
    inline = env.get(inline_var)
    if inline:
        return inline
    file_path = env.get(file_var)
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


@dataclass(frozen=True)
class GatewayConfig:
    app_env: str
    billing_mode: str
    merchant_api_key: str
    webhook_secret: str
    webhook_url: str | None
    # PostgREST persistence (production): the gateway reaches its private
    # `mock_gateway` schema through PostgREST with its own role JWT. When both
    # are set a DB-backed store is used; otherwise the in-memory test double.
    postgrest_url: str | None
    database_jwt: str | None
    database_schema: str
    renewal_period_seconds: float
    webhook_max_attempts: int
    webhook_backoff_base_seconds: float
    upi_timeout_seconds: float
    public_base_path: str

    @property
    def uses_database(self) -> bool:
        return bool(self.postgrest_url and self.database_jwt)

    @property
    def is_demo(self) -> bool:
        return self.billing_mode == BILLING_MODE_DEMO

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS


def load_config(environ: dict[str, str] | None = None) -> GatewayConfig:
    """Build and validate the gateway configuration.

    Refuses to start in production unless ``BILLING_MODE=demo`` (the gateway is
    a demo-only money-free stand-in). Always requires a merchant API key and a
    webhook signing secret so requests and webhooks are authenticated/signed.
    """
    env = dict(os.environ if environ is None else environ)

    def get(name: str, default: str | None = None) -> str | None:
        value = env.get(name)
        return value if value not in (None, "") else default

    app_env = get("APP_ENV", "production") or "production"
    billing_mode = (get("BILLING_MODE", "") or "").strip().lower()

    if app_env.strip().lower() in PRODUCTION_ENVS and billing_mode != BILLING_MODE_DEMO:
        raise GatewayConfigError(
            "the mock payment gateway refuses to start in production unless "
            f"BILLING_MODE=demo (got BILLING_MODE={billing_mode!r})"
        )
    # Outside production, default to demo so tests and the CPU stage can run it.
    if not billing_mode:
        billing_mode = BILLING_MODE_DEMO

    merchant_api_key = _read_secret(
        "MERCHANT_API_KEY", "MERCHANT_API_KEY_FILE", env=env, default=None
    )
    if not merchant_api_key:
        if app_env.strip().lower() in PRODUCTION_ENVS:
            raise GatewayConfigError("MERCHANT_API_KEY (or MERCHANT_API_KEY_FILE) is required")
        merchant_api_key = "test-merchant-key"

    webhook_secret = _read_secret(
        "MOCK_GATEWAY_WEBHOOK_SECRET", "MOCK_GATEWAY_WEBHOOK_SECRET_FILE", env=env, default=None
    )
    if not webhook_secret:
        if app_env.strip().lower() in PRODUCTION_ENVS:
            raise GatewayConfigError(
                "MOCK_GATEWAY_WEBHOOK_SECRET (or MOCK_GATEWAY_WEBHOOK_SECRET_FILE) is required"
            )
        webhook_secret = "test-webhook-secret"

    # PostgREST persistence for the private mock_gateway schema. The JWT's role
    # claim is `mock_gateway_role`; it is mounted only into the payment-gateway
    # service (like Task 18's supabase_worker_key). In production a demo gateway
    # must persist, so require both the URL and the JWT there.
    postgrest_url = get("MOCK_GATEWAY_POSTGREST_URL")
    database_jwt = _read_secret(
        "MOCK_GATEWAY_DB_JWT", "MOCK_GATEWAY_DB_JWT_FILE", env=env, default=None
    )
    if app_env.strip().lower() in PRODUCTION_ENVS and not (postgrest_url and database_jwt):
        raise GatewayConfigError(
            "a production demo gateway must persist: set MOCK_GATEWAY_POSTGREST_URL and "
            "MOCK_GATEWAY_DB_JWT (or MOCK_GATEWAY_DB_JWT_FILE)"
        )

    return GatewayConfig(
        app_env=app_env,
        billing_mode=billing_mode,
        merchant_api_key=merchant_api_key,
        webhook_secret=webhook_secret,
        webhook_url=get("MOCK_GATEWAY_WEBHOOK_URL"),
        postgrest_url=postgrest_url,
        database_jwt=database_jwt,
        database_schema=get("MOCK_GATEWAY_DB_SCHEMA", "mock_gateway") or "mock_gateway",
        renewal_period_seconds=float(get("MOCK_GATEWAY_RENEWAL_PERIOD_SECONDS", "2592000")),
        webhook_max_attempts=int(get("MOCK_GATEWAY_WEBHOOK_MAX_ATTEMPTS", "5")),
        webhook_backoff_base_seconds=float(
            get("MOCK_GATEWAY_WEBHOOK_BACKOFF_SECONDS", "1.0")
        ),
        upi_timeout_seconds=float(get("MOCK_GATEWAY_UPI_TIMEOUT_SECONDS", "120")),
        public_base_path=get("MOCK_GATEWAY_BASE_PATH", "/pay") or "/pay",
    )


@lru_cache
def get_config() -> GatewayConfig:
    return load_config()
