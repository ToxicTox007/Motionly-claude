"""Manual, operator-authorized live-provider runs for the Task 17 ports.

Every test here is marked ``@pytest.mark.live_provider`` and is skipped unless
``MOTIONLY_LIVE_INFERENCE=1`` is set — the only case the Task 1 no-network guard
in ``tests/conftest.py`` stands down. These are never run in CI (invariant 9).
See ``docs/ai-registry/live-runs.md`` for the workflow and the evidence log.
"""
