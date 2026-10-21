"""Task 26 — live-collaboration broadcasts over Supabase Realtime.

After a save, the API tells every other editor of the same project what changed
so their browser can reload just the affected panel without a full reload. The
message carries only ids and revisions — never plan content — so Realtime never
becomes a second copy of the data and a listener learns only *that* something
changed, then fetches it over the normal, RLS-guarded API.

Transport: a single HTTP POST to Realtime's REST broadcast endpoint with the
service-role key. This is the smallest reliable mechanism (no extra WebSocket to
keep alive, no second write path): Realtime receives the request and fans the
message out to the connected private-channel subscribers. The topic is the
Task 25 private topic ``project:<project-uuid>`` whose RLS
(``realtime.motionly_can_access_topic``) already limits delivery to active
members of the project's workspace.

A broadcast is best-effort: a failure here must never fail the save that
triggered it, so the notifier swallows and logs transport errors.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

EVENT_MANIFEST_UPDATED = "manifest_updated"
EVENT_PLAN_REVISION_UPDATED = "plan_revision_updated"


def project_topic(project_id: UUID | str) -> str:
    """The Task 25 private topic for a project's live channel."""
    return f"project:{project_id}"


class CollaborationNotifierProtocol(Protocol):
    """What the save paths depend on; satisfied by the real and fake clients."""

    def notify_manifest_updated(
        self, *, project_id: UUID | str, workspace_id: UUID | str, revision: int
    ) -> None: ...

    def notify_plan_revision_updated(
        self,
        *,
        project_id: UUID | str,
        workspace_id: UUID | str,
        plan_id: UUID | str,
        revision: int,
    ) -> None: ...


class CollaborationNotifier:
    """Broadcasts change notifications via Realtime's REST broadcast endpoint."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._endpoint = f"{supabase_url.rstrip('/')}/realtime/v1/api/broadcast"
        self._service_role_key = service_role_key
        self._timeout = timeout_seconds

    def notify_manifest_updated(
        self, *, project_id: UUID | str, workspace_id: UUID | str, revision: int
    ) -> None:
        self._broadcast(
            project_id=project_id,
            event=EVENT_MANIFEST_UPDATED,
            payload={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "revision": int(revision),
            },
        )

    def notify_plan_revision_updated(
        self,
        *,
        project_id: UUID | str,
        workspace_id: UUID | str,
        plan_id: UUID | str,
        revision: int,
    ) -> None:
        self._broadcast(
            project_id=project_id,
            event=EVENT_PLAN_REVISION_UPDATED,
            payload={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "plan_id": str(plan_id),
                "revision": int(revision),
            },
        )

    def _broadcast(
        self, *, project_id: UUID | str, event: str, payload: dict[str, Any]
    ) -> None:
        body = {
            "messages": [
                {
                    "topic": project_topic(project_id),
                    "event": event,
                    # Private channel: only authenticated members receive it
                    # (matches the clients' config.private and Task 25 RLS).
                    "private": True,
                    "payload": payload,
                }
            ]
        }
        try:
            response = httpx.post(
                self._endpoint,
                headers={
                    "apikey": self._service_role_key,
                    "authorization": f"Bearer {self._service_role_key}",
                    "content-type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            # Best-effort: a dropped broadcast must never fail the save. The
            # saver already persisted the new revision; a missed notification
            # only means a peer reloads on its next poll/save instead of now.
            logger.warning(
                "collaboration_broadcast_failed",
                extra={"event": event, "error_type": type(exc).__name__},
            )
            return
        if response.status_code >= 400:
            logger.warning(
                "collaboration_broadcast_rejected",
                extra={"event": event, "status_code": response.status_code},
            )


class FakeCollaborationNotifier:
    """Records broadcasts in memory so unit tests can assert what was emitted.

    Used as the default notifier whenever Supabase is not configured (unit tests
    and local dev), mirroring the real notifier's interface exactly.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def notify_manifest_updated(
        self, *, project_id: UUID | str, workspace_id: UUID | str, revision: int
    ) -> None:
        self.messages.append(
            {
                "topic": project_topic(project_id),
                "event": EVENT_MANIFEST_UPDATED,
                "payload": {
                    "workspace_id": str(workspace_id),
                    "project_id": str(project_id),
                    "revision": int(revision),
                },
            }
        )

    def notify_plan_revision_updated(
        self,
        *,
        project_id: UUID | str,
        workspace_id: UUID | str,
        plan_id: UUID | str,
        revision: int,
    ) -> None:
        self.messages.append(
            {
                "topic": project_topic(project_id),
                "event": EVENT_PLAN_REVISION_UPDATED,
                "payload": {
                    "workspace_id": str(workspace_id),
                    "project_id": str(project_id),
                    "plan_id": str(plan_id),
                    "revision": int(revision),
                },
            }
        )


def notifier_from_settings(settings: Any) -> CollaborationNotifierProtocol:
    """Real notifier when Supabase is configured; the recording fake otherwise."""
    supabase_url = getattr(settings, "supabase_url", None)
    service_role_key = getattr(settings, "supabase_service_role_key", None)
    if supabase_url and service_role_key:
        return CollaborationNotifier(
            supabase_url=str(supabase_url),
            service_role_key=str(service_role_key),
        )
    return FakeCollaborationNotifier()
