from __future__ import annotations

import os
from collections.abc import Mapping

EXTERNAL_VERIFICATION_TARGET = "staging-sandbox"


def live_provider_contracts_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """Return whether live provider tests are deliberately authorized.

    The default is hermetic. Once a caller opts in, every approval value is required
    before test collection can construct a provider or make a network request.
    """
    values = os.environ if environment is None else environment
    if values.get("LIVE_PROVIDER_CONTRACTS") != "true":
        return False

    authorization = str(values.get("EXTERNAL_VERIFICATION_AUTHORIZATION") or "").strip()
    target = str(values.get("EXTERNAL_VERIFICATION_TARGET") or "").strip()
    run_id = str(values.get("EXTERNAL_VERIFICATION_RUN_ID") or "").strip()
    if len(authorization) < 16:
        raise RuntimeError("External provider verification requires protected authorization.")
    if target != EXTERNAL_VERIFICATION_TARGET:
        raise RuntimeError("External provider verification target must be staging-sandbox.")
    if not run_id or len(run_id) > 200:
        raise RuntimeError("External provider verification requires a bounded run identifier.")
    return True
