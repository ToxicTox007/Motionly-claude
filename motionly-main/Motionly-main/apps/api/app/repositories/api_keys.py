"""Repository for workspace API keys (Task 24).

Agency-plan workspaces authenticate to the Motionly API with keys of the form
``mtly_<random>``. Only a SHA-256 fingerprint of each key is ever stored; the
raw secret is returned to the creator exactly once and never persisted or
logged. A short display prefix (``mtly_ab``) and the last four characters are
kept so a key is recognisable in the UI without revealing it.

Authentication looks a key up by fingerprint and compares in constant time so
the lookup never leaks (via timing) whether a given fingerprint exists.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.api_keys import ApiKeyResponse
from app.services.supabase import SupabaseRestClient

API_KEY_COLUMNS = (
    "id,workspace_id,name,fingerprint,prefix,last4,created_by,created_at,last_used_at,revoked_at"
)

# Raw keys look like ``mtly_<43 url-safe base64 chars>`` (256 bits of entropy).
KEY_SCHEME_PREFIX = "mtly_"
_SECRET_BYTES = 32
# The display prefix keeps ``mtly_`` plus the first few secret characters.
_PREFIX_SECRET_CHARS = 6


def key_fingerprint(raw_key: str) -> str:
    """SHA-256 fingerprint of a raw API key. Only this is ever persisted."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str, str]:
    """Mint a new key.

    Returns ``(raw_key, fingerprint, prefix, last4)``. Only the latter three are
    stored; ``raw_key`` is shown to the creator once and then discarded.
    """
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    raw_key = f"{KEY_SCHEME_PREFIX}{secret}"
    prefix = f"{KEY_SCHEME_PREFIX}{secret[:_PREFIX_SECRET_CHARS]}"
    last4 = secret[-4:]
    return raw_key, key_fingerprint(raw_key), prefix, last4


@dataclass
class ApiKeyRecord:
    id: UUID
    workspace_id: UUID
    name: str
    fingerprint: str
    prefix: str
    last4: str
    created_by: UUID
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None

    def to_response(self) -> ApiKeyResponse:
        return ApiKeyResponse(
            id=self.id,
            name=self.name,
            prefix=self.prefix,
            last4=self.last4,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            revoked_at=self.revoked_at,
        )


class InMemoryApiKeyRepository:
    def __init__(self) -> None:
        self._keys: dict[UUID, ApiKeyRecord] = {}
        self._lock = RLock()

    def create_key(
        self,
        *,
        workspace_id: UUID,
        name: str,
        fingerprint: str,
        prefix: str,
        last4: str,
        created_by: UUID,
    ) -> ApiKeyRecord:
        with self._lock:
            record = ApiKeyRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                name=name,
                fingerprint=fingerprint,
                prefix=prefix,
                last4=last4,
                created_by=created_by,
            )
            self._keys[record.id] = record
            return record

    def list_keys(self, *, workspace_id: UUID) -> list[ApiKeyRecord]:
        with self._lock:
            keys = [k for k in self._keys.values() if k.workspace_id == workspace_id]
        return sorted(keys, key=lambda k: k.created_at, reverse=True)

    def revoke_key(self, *, workspace_id: UUID, key_id: UUID) -> ApiKeyRecord:
        with self._lock:
            record = self._keys.get(key_id)
            if record is None or record.workspace_id != workspace_id:
                raise ApiError(404, "api_key_not_found", "API key not found.")
            if record.revoked_at is None:
                record.revoked_at = datetime.now(UTC)
            return record

    def resolve_live_key(self, *, fingerprint: str) -> ApiKeyRecord | None:
        """Return the live (non-revoked) key with this fingerprint, if any.

        The comparison is constant-time: every candidate fingerprint is checked
        so the lookup does not reveal, via timing, whether a fingerprint exists.
        """
        with self._lock:
            match: ApiKeyRecord | None = None
            for record in self._keys.values():
                if record.revoked_at is not None:
                    continue
                if hmac.compare_digest(record.fingerprint, fingerprint):
                    match = record
            return match

    def touch_last_used(self, *, key_id: UUID, now: datetime | None = None) -> None:
        with self._lock:
            record = self._keys.get(key_id)
            if record is not None:
                record.last_used_at = now or datetime.now(UTC)


class SupabaseApiKeyRepository(InMemoryApiKeyRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__()
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="api_keys",
            metrics_registry=metrics_registry,
        )

    def create_key(
        self,
        *,
        workspace_id: UUID,
        name: str,
        fingerprint: str,
        prefix: str,
        last4: str,
        created_by: UUID,
    ) -> ApiKeyRecord:
        response = self.client.request(
            "POST",
            "/workspace_api_keys",
            headers={"prefer": "return=representation"},
            json={
                "workspace_id": str(workspace_id),
                "name": name,
                "fingerprint": fingerprint,
                "prefix": prefix,
                "last4": last4,
                "created_by": str(created_by),
            },
        )
        self._raise_for_supabase(response)
        return self._from_row(response.json()[0])

    def list_keys(self, *, workspace_id: UUID) -> list[ApiKeyRecord]:
        response = self.client.request(
            "GET",
            "/workspace_api_keys",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": API_KEY_COLUMNS,
                "order": "created_at.desc",
            },
        )
        self._raise_for_supabase(response)
        return [self._from_row(row) for row in response.json()]

    def revoke_key(self, *, workspace_id: UUID, key_id: UUID) -> ApiKeyRecord:
        response = self.client.request(
            "PATCH",
            "/workspace_api_keys",
            headers={"prefer": "return=representation"},
            params={
                "id": f"eq.{key_id}",
                "workspace_id": f"eq.{workspace_id}",
                "revoked_at": "is.null",
                "select": API_KEY_COLUMNS,
            },
            json={"revoked_at": datetime.now(UTC).isoformat()},
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if rows:
            return self._from_row(rows[0])
        # Already revoked or not found: read it back so revoke stays idempotent.
        existing = self.client.request(
            "GET",
            "/workspace_api_keys",
            params={
                "id": f"eq.{key_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": API_KEY_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(existing)
        found = existing.json()
        if not found:
            raise ApiError(404, "api_key_not_found", "API key not found.")
        return self._from_row(found[0])

    def resolve_live_key(self, *, fingerprint: str) -> ApiKeyRecord | None:
        # A unique fingerprint means at most one row; the partial index on
        # (fingerprint) where revoked_at is null keeps this lookup fast.
        response = self.client.request(
            "GET",
            "/workspace_api_keys",
            params={
                "fingerprint": f"eq.{fingerprint}",
                "revoked_at": "is.null",
                "select": API_KEY_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return None
        record = self._from_row(rows[0])
        # Constant-time re-check so a crafted equality filter cannot stand in
        # for the fingerprint match.
        if not hmac.compare_digest(record.fingerprint, fingerprint):
            return None
        return record

    def touch_last_used(self, *, key_id: UUID, now: datetime | None = None) -> None:
        stamp = (now or datetime.now(UTC)).isoformat()
        response = self.client.request(
            "PATCH",
            "/workspace_api_keys",
            params={"id": f"eq.{key_id}"},
            json={"last_used_at": stamp},
        )
        self._raise_for_supabase(response)

    @staticmethod
    def _from_row(row: dict[str, object]) -> ApiKeyRecord:
        return ApiKeyRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            name=str(row["name"]),
            fingerprint=str(row["fingerprint"]),
            prefix=str(row["prefix"]),
            last4=str(row["last4"]),
            created_by=UUID(str(row["created_by"])),
            created_at=_parse_datetime(str(row["created_at"])),
            last_used_at=_parse_optional_datetime(row.get("last_used_at")),
            revoked_at=_parse_optional_datetime(row.get("revoked_at")),
        )

    @staticmethod
    def _raise_for_supabase(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", response.text)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_optional_datetime(value: object) -> datetime | None:
    if not value:
        return None
    return _parse_datetime(str(value))
