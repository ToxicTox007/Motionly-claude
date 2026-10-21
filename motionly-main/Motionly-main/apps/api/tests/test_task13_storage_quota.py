"""Task 13 / B23: the dashboard storage gauge reads the real workspace quota.

A workspace-level endpoint returns the StorageQuotaService usage for the actor's
workspace (no fabricated numbers). The dashboard sources its gauge from here.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.services.storage_quota import QuotaUsage


class _StubQuotaService:
    def __init__(self, usage: QuotaUsage) -> None:
        self._usage = usage
        self.requested_for = None

    async def get_usage(self, workspace_id):
        self.requested_for = workspace_id
        return self._usage


def test_workspace_storage_quota_returns_real_usage() -> None:
    from app.api.media import get_workspace_storage_quota

    workspace_id = uuid4()
    usage = QuotaUsage(
        used_bytes=2 * 1024**3,
        reserved_bytes=0,
        max_bytes=5 * 1024**3,
        used_object_count=4,
        reserved_object_count=0,
        max_objects=100,
        percentage=40.0,
    )
    service = _StubQuotaService(usage)
    actor = SimpleNamespace(workspace_id=workspace_id, user_id=uuid4())

    response = asyncio.run(get_workspace_storage_quota(actor=actor, quota_service=service))

    assert service.requested_for == workspace_id
    assert response.used_bytes == usage.used_bytes
    assert response.max_bytes == usage.max_bytes
    assert response.percentage == 40.0
    assert response.used_object_count == 4
