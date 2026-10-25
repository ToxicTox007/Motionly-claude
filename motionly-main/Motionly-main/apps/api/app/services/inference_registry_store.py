"""Database registry store for inference adapters, models, routes and secrets.

Plan Task 20, Section 2.4 (Tier 3). The declarative file registry stays a
complete fallback; the database registry is **additive** and off unless
``AI_REGISTRY_DATABASE_ENABLED`` is true. Both sources are merged by the runtime
(Task 6/20): a file id and a database id must never collide.

This module provides two interchangeable repositories:

* :class:`InMemoryInferenceRegistryRepository` — a deterministic store for tests
  and local development;
* :class:`SupabaseInferenceRegistryRepository` — PostgREST-backed, following the
  ``app/repositories/scale.py`` pattern (service-role :class:`SupabaseRestClient`).

Both expose CRUD for adapters, models, routes (with the Section 2.11 ``offering``
block) and **write-only** secrets (a secret read returns metadata only — name,
scope, fingerprint, key version, timestamps — never a ciphertext or plaintext),
the registry version counter, and :meth:`load_document`, which returns a
:class:`~app.services.inference_registry.RegistryDocument` whose every record
carries ``source="database"``.

Secret decryption lives behind :meth:`open_secret`, which reads the ciphertext
row and hands it to the injected :class:`~app.services.inference_secret_store.InferenceSecretStore`.
That method is deliberately NOT on any worker-reachable code path: the worker
builds the route catalog (adapters/models/routes) but never reads ``ai_secrets``.
Every registry write bumps the version counter in the same transaction (the
database enforces this via a trigger; the in-memory store bumps it inline).

Nothing here logs a plaintext secret, a ciphertext or a KEK.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.services.inference_registry import (
    Diagnostic,
    RegistryDocument,
    load_registry_document,
)
from app.services.inference_secret_store import InferenceSecretStore, SealedSecret

# PostgREST relation names (Task 20 migration). Literal paths so the static
# schema/worker-grant analysers (tests/schema_usage.py) see exactly these reads.
_ADAPTERS = "/ai_adapters"
_MODELS = "/ai_models"
_ROUTES = "/ai_routes"
_SECRETS = "/ai_secrets"
_VERSION = "/ai_registry_version"

_VERSION_SINGLETON_ID = 1


class RegistryStoreError(RuntimeError):
    """A registry store operation failed. Never contains a secret value."""


class IdCollisionError(RegistryStoreError):
    """A database id collides with a file-registry id (plan Section 2.4)."""


@dataclass(frozen=True)
class SecretMetadata:
    """What a secret read may expose (plan Section 2.4): never a value."""

    name: str
    scope: str
    fingerprint: str
    key_version: str
    created_by: str | None = None
    created_at: datetime | None = None
    rotated_at: datetime | None = None
    last_used_at: datetime | None = None


def _as_route_dict(record: Mapping[str, Any]) -> dict[str, Any]:
    """A database route row rendered as the JSON shape the file loader accepts.

    Only keys present are emitted, so the strict loader's defaults apply; the
    ``offering`` jsonb column is passed through verbatim.
    """
    out: dict[str, Any] = {}
    for key in (
        "id",
        "capability",
        "adapter_id",
        "model_id",
        "model_ref",
        "model_revision",
        "allow_provider_managed_revision",
        "priority",
        "strategy",
        "provider",
        "pricing",
        "explicitly_free",
        "budget",
        "timeout_s",
        "compute_requirement",
        "requires_gpu",
        "limits",
        "enabled",
        "scope",
        "embedding_space",
        "params",
        "mapping",
        "upload_filename",
        "label_aliases",
        "input_transform",
        "offering",
    ):
        if key in record and record[key] is not None:
            out[key] = record[key]
    return out


def _as_adapter_dict(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "id",
        "type",
        "base_url",
        "network",
        "auth",
        "timeout_s",
        "health",
        "extra_headers",
        "extra_body",
        "api_version",
        "data_processor",
    ):
        if key in record and record[key] is not None:
            out[key] = record[key]
    return out


def _as_model_dict(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "id",
        "model_id",
        "revision",
        "licence",
        "commercial_use",
        "source_type",
        "source_url",
        "capabilities",
        "min_vram_gb",
        "weights_sha256",
        "verified_at",
    ):
        if key in record and record[key] is not None:
            out[key] = record[key]
    return out


def _document_from_rows(
    *,
    adapters: list[Mapping[str, Any]],
    models: list[Mapping[str, Any]],
    routes: list[Mapping[str, Any]],
) -> tuple[RegistryDocument | None, list[Diagnostic]]:
    """Parse database rows through the strict file loader, then mark the source."""
    payload = {
        "version": 1,
        "adapters": [_as_adapter_dict(a) for a in adapters],
        "models": [_as_model_dict(m) for m in models],
        "routes": [_as_route_dict(r) for r in routes],
    }
    document, diagnostics = load_registry_document(payload)
    if document is None:
        return None, diagnostics
    document = replace(
        document,
        routes=tuple(replace(route, source="database") for route in document.routes),
    )
    return document, diagnostics


class InMemoryInferenceRegistryRepository:
    """A deterministic in-memory registry store (tests, local dev).

    Rows are plain dicts shaped like the file registry. Every write bumps the
    version counter inline, mirroring the database trigger.
    """

    def __init__(self, *, secret_store: InferenceSecretStore | None = None) -> None:
        self._adapters: dict[str, dict[str, Any]] = {}
        self._models: dict[str, dict[str, Any]] = {}
        self._routes: dict[str, dict[str, Any]] = {}
        self._secrets: dict[tuple[str, str], dict[str, Any]] = {}
        self._version = 0
        self._lock = threading.RLock()
        self._secret_store = secret_store

    # -- version -----------------------------------------------------------
    def registry_version(self) -> int:
        with self._lock:
            return self._version

    def _bump(self) -> None:
        self._version += 1

    # -- adapters / models / routes CRUD ----------------------------------
    def upsert_adapter(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            self._adapters[str(record["id"])] = dict(record)
            self._bump()

    def upsert_model(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            self._models[str(record["id"])] = dict(record)
            self._bump()

    def upsert_route(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            self._routes[str(record["id"])] = dict(record)
            self._bump()

    def delete_adapter(self, adapter_id: str) -> None:
        with self._lock:
            if self._adapters.pop(adapter_id, None) is not None:
                self._bump()

    def delete_model(self, model_id: str) -> None:
        with self._lock:
            if self._models.pop(model_id, None) is not None:
                self._bump()

    def delete_route(self, route_id: str) -> None:
        with self._lock:
            if self._routes.pop(route_id, None) is not None:
                self._bump()

    def list_adapters(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self._adapters.values()]

    def list_models(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._models.values()]

    def list_routes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._routes.values()]

    # -- secrets (write-only reads return metadata) -----------------------
    def put_secret(
        self, *, name: str, scope: str, plaintext: str, created_by: str | None = None
    ) -> SecretMetadata:
        if self._secret_store is None:
            raise RegistryStoreError("no secret store is configured for database secrets")
        sealed = self._secret_store.seal(name=name, scope=scope, plaintext=plaintext)
        now = datetime.now(UTC)
        with self._lock:
            existing = self._secrets.get((scope, name))
            row = {
                "name": name,
                "scope": scope,
                "ciphertext_b64": sealed.ciphertext_b64,
                "nonce_b64": sealed.nonce_b64,
                "key_version": sealed.key_version,
                "fingerprint": sealed.fingerprint,
                "created_by": created_by,
                "created_at": existing["created_at"] if existing else now,
                "rotated_at": now if existing else None,
                "last_used_at": existing["last_used_at"] if existing else None,
            }
            self._secrets[(scope, name)] = row
            self._bump()
        return self._metadata(row)

    def list_secret_metadata(self) -> list[SecretMetadata]:
        with self._lock:
            return [self._metadata(row) for row in self._secrets.values()]

    def get_secret_metadata(self, *, name: str, scope: str) -> SecretMetadata | None:
        with self._lock:
            row = self._secrets.get((scope, name))
            return self._metadata(row) if row is not None else None

    def delete_secret(self, *, name: str, scope: str) -> None:
        with self._lock:
            if self._secrets.pop((scope, name), None) is not None:
                self._bump()

    def open_secret(self, *, name: str, scope: str = "global") -> str:
        """Decrypt a secret (plan Task 20). API/ops only — never worker-reachable."""
        if self._secret_store is None:
            raise RegistryStoreError("no secret store is configured for database secrets")
        with self._lock:
            row = self._secrets.get((scope, name))
        if row is None:
            raise RegistryStoreError(f"secret {name!r} is not present in the database")
        return self._secret_store.open(
            name=name,
            scope=scope,
            ciphertext_b64=row["ciphertext_b64"],
            nonce_b64=row["nonce_b64"],
            key_version=row["key_version"],
        )

    def secret_rows_for_reencrypt(self) -> list[dict[str, Any]]:
        """Raw sealed rows for the re-encryption CLI (plan Task 20)."""
        with self._lock:
            return [dict(row) for row in self._secrets.values()]

    def store_resealed_secret(self, *, name: str, scope: str, sealed: SealedSecret) -> None:
        with self._lock:
            row = self._secrets.get((scope, name))
            if row is None:
                raise RegistryStoreError(f"secret {name!r} is not present in the database")
            row.update(
                ciphertext_b64=sealed.ciphertext_b64,
                nonce_b64=sealed.nonce_b64,
                key_version=sealed.key_version,
                fingerprint=sealed.fingerprint,
                rotated_at=datetime.now(UTC),
            )
            self._bump()

    @staticmethod
    def _metadata(row: Mapping[str, Any]) -> SecretMetadata:
        return SecretMetadata(
            name=row["name"],
            scope=row["scope"],
            fingerprint=row["fingerprint"],
            key_version=row["key_version"],
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
            rotated_at=row.get("rotated_at"),
            last_used_at=row.get("last_used_at"),
        )

    # -- document ----------------------------------------------------------
    def load_document(self) -> tuple[RegistryDocument | None, list[Diagnostic]]:
        with self._lock:
            adapters = [dict(a) for a in self._adapters.values()]
            models = [dict(m) for m in self._models.values()]
            routes = [dict(r) for r in self._routes.values()]
        return _document_from_rows(adapters=adapters, models=models, routes=routes)


class SupabaseInferenceRegistryRepository:
    """PostgREST-backed registry store (plan Task 20), following scale.py.

    Reads of adapters/models/routes build the catalog and are worker-reachable;
    the secret ciphertext read (:meth:`open_secret`) is API/ops only.
    """

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        secret_store: InferenceSecretStore | None = None,
        metrics_registry: object | None = None,
    ) -> None:
        from app.services.supabase import SupabaseRestClient

        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="inference_registry",
            metrics_registry=metrics_registry,
            timeout_seconds=15,
        )
        self._secret_store = secret_store

    def _rows(self, response) -> list[dict[str, Any]]:
        if response.status_code >= 400:
            raise RegistryStoreError(
                f"registry store request failed with status {response.status_code}"
            )
        data = response.json()
        return list(data) if isinstance(data, list) else [data]

    # -- version -----------------------------------------------------------
    def registry_version(self) -> int:
        response = self.client.request(
            "GET",
            _VERSION,
            params={"select": "id,version", "id": f"eq.{_VERSION_SINGLETON_ID}", "limit": "1"},
        )
        rows = self._rows(response)
        return int(rows[0]["version"]) if rows else 0

    # -- reads that build the catalog (worker-reachable) -------------------
    def list_adapters(self) -> list[dict[str, Any]]:
        return self._rows(self.client.request("GET", _ADAPTERS, params={"select": "*"}))

    def list_models(self) -> list[dict[str, Any]]:
        return self._rows(self.client.request("GET", _MODELS, params={"select": "*"}))

    def list_routes(self) -> list[dict[str, Any]]:
        return self._rows(self.client.request("GET", _ROUTES, params={"select": "*"}))

    def load_document(self) -> tuple[RegistryDocument | None, list[Diagnostic]]:
        return _document_from_rows(
            adapters=self.list_adapters(),
            models=self.list_models(),
            routes=self.list_routes(),
        )

    # -- writes (each bumps the version via the migration trigger) ---------
    def upsert_adapter(self, record: Mapping[str, Any]) -> None:
        self._rows(
            self.client.request(
                "POST",
                _ADAPTERS,
                params={"on_conflict": "id"},
                headers={"prefer": "resolution=merge-duplicates,return=representation"},
                json=dict(record),
            )
        )

    def upsert_model(self, record: Mapping[str, Any]) -> None:
        self._rows(
            self.client.request(
                "POST",
                _MODELS,
                params={"on_conflict": "id"},
                headers={"prefer": "resolution=merge-duplicates,return=representation"},
                json=dict(record),
            )
        )

    def upsert_route(self, record: Mapping[str, Any]) -> None:
        self._rows(
            self.client.request(
                "POST",
                _ROUTES,
                params={"on_conflict": "id"},
                headers={"prefer": "resolution=merge-duplicates,return=representation"},
                json=dict(record),
            )
        )

    def delete_route(self, route_id: str) -> None:
        self.client.request("DELETE", _ROUTES, params={"id": f"eq.{route_id}"})

    # -- secrets (write-only; reads return metadata) ----------------------
    def put_secret(
        self, *, name: str, scope: str, plaintext: str, created_by: str | None = None
    ) -> SecretMetadata:
        if self._secret_store is None:
            raise RegistryStoreError("no secret store is configured for database secrets")
        sealed = self._secret_store.seal(name=name, scope=scope, plaintext=plaintext)
        row = {
            "name": name,
            "scope": scope,
            "ciphertext": sealed.ciphertext_b64,
            "nonce": sealed.nonce_b64,
            "key_version": sealed.key_version,
            "fingerprint": sealed.fingerprint,
            "created_by": created_by,
        }
        self._rows(
            self.client.request(
                "POST",
                _SECRETS,
                params={"on_conflict": "scope,name"},
                headers={"prefer": "resolution=merge-duplicates,return=representation"},
                json=row,
            )
        )
        return SecretMetadata(
            name=name,
            scope=scope,
            fingerprint=sealed.fingerprint,
            key_version=sealed.key_version,
            created_by=created_by,
        )

    def list_secret_metadata(self) -> list[SecretMetadata]:
        rows = self._rows(
            self.client.request(
                "GET",
                _SECRETS,
                params={
                    "select": "name,scope,fingerprint,key_version,created_by,"
                    "created_at,rotated_at,last_used_at"
                },
            )
        )
        return [
            SecretMetadata(
                name=row["name"],
                scope=row["scope"],
                fingerprint=row["fingerprint"],
                key_version=row["key_version"],
                created_by=row.get("created_by"),
            )
            for row in rows
        ]

    # -- re-encryption support (ops CLI only; never worker-reachable) ------
    def secret_rows_for_reencrypt(self) -> list[dict[str, Any]]:
        """Sealed rows (including ciphertext) for the re-encryption CLI.

        Reads ``ai_secrets`` ciphertext. Called only by
        ``app.ops.reencrypt_inference_secrets`` and the admin context, never by
        the runtime builder, so the worker is never granted ``ai_secrets``.
        """
        return self._rows(
            self.client.request(
                "GET",
                _SECRETS,
                params={"select": "name,scope,ciphertext,nonce,key_version,fingerprint"},
            )
        )

    def store_resealed_secret(self, *, name: str, scope: str, sealed: SealedSecret) -> None:
        self.client.request(
            "PATCH",
            _SECRETS,
            params={"name": f"eq.{name}", "scope": f"eq.{scope}"},
            json={
                "ciphertext": sealed.ciphertext_b64,
                "nonce": sealed.nonce_b64,
                "key_version": sealed.key_version,
                "fingerprint": sealed.fingerprint,
                "rotated_at": datetime.now(UTC).isoformat(),
            },
        )


__all__ = [
    "IdCollisionError",
    "InMemoryInferenceRegistryRepository",
    "RegistryStoreError",
    "SecretMetadata",
    "SupabaseInferenceRegistryRepository",
]
