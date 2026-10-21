"""Entrypoint: ``python -m app.mock_gateway``.

Builds the app (which validates configuration and refuses to start in
production without ``BILLING_MODE=demo``) and serves it with uvicorn.
"""

from __future__ import annotations

import os

import uvicorn

from .config import get_config
from .main import create_app


def main() -> None:
    config = get_config()
    app = create_app(config)
    uvicorn.run(
        app,
        host=os.environ.get("MOCK_GATEWAY_HOST", "0.0.0.0"),  # noqa: S104 - container service
        port=int(os.environ.get("MOCK_GATEWAY_PORT", "8010")),
        log_level="info",
        # One process, whatever WEB_CONCURRENCY the API image sets: renewals
        # are scheduled in-process and must not run twice.
        workers=1,
    )


if __name__ == "__main__":
    main()
