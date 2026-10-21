"""B26 regression guard: secret tokens must be compared in constant time.

A direct ``==``/``!=`` comparison of a secret (admin / worker / metrics tokens,
the origin-verify secret, webhook signatures, share / reset tokens or
local-storage signatures) leaks timing information that lets an attacker recover
the secret byte by byte. Every such comparison must go through
``hmac.compare_digest`` (``app.core.security.constant_time_token_matches``).

This test scans the API source for naive equality comparisons that mention a
known secret name so that a regression fails here instead of shipping.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Attribute / variable fragments that name a raw secret value. A comparison of
# a *digest* of a secret (``token_digest(...)``) is safe, so the scanner ignores
# lines that hash first.
SECRET_NAME_FRAGMENTS = (
    "admin_api_token",
    "worker_api_token",
    "metrics_bearer_token",
    "x_motionly_worker_token",
    "x_motionly_admin_token",
    "origin_verify",
    "webhook_secret",
    "gateway_signature",
    "share_token",
    "reset_token",
    "local_storage_signature",
    "storage_signature",
)

# A ``Bearer {secret}`` f-string compared with ``==``/``!=`` is the classic
# non-constant-time bearer check.
BEARER_COMPARISON = re.compile(r'(==|!=)\s*f?"Bearer \{')

# ``<something secret> == …`` or ``… == <something secret>``.
def _mentions_secret(line: str) -> bool:
    lowered = line.lower()
    return any(fragment in lowered for fragment in SECRET_NAME_FRAGMENTS)


def _is_comparison(line: str) -> bool:
    stripped = line.strip()
    # Ignore comparisons against a hashed value and assignment of digests.
    if "token_digest" in stripped or "compare_digest" in stripped:
        return False
    if "constant_time_token_matches" in stripped:
        return False
    if BEARER_COMPARISON.search(stripped):
        return True
    return bool(re.search(r"(==|!=)", stripped)) and _mentions_secret(stripped)


def test_no_naive_secret_equality_comparisons() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "==" not in code and "!=" not in code:
                continue
            if _is_comparison(code):
                offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Secret tokens must be compared with constant_time_token_matches / "
        "hmac.compare_digest, not ==/!=:\n" + "\n".join(offenders)
    )
