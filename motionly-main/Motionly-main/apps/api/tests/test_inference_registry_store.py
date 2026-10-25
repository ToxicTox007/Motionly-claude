"""Tests for the database registry store, runtime holder, db: secrets and the
re-encryption CLI (plan Task 20).

Postgres cannot run here, so the Supabase repository's SQL behaviour is covered
by supabase/tests/inference_registry_test.sql (static) and in CI; these tests
exercise the in-memory repository, the runtime's file+database merge and the
RuntimeHolder with that repository.
"""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace

import pytest

from app.ops.reencrypt_inference_secrets import reencrypt_secrets
from app.services.inference_registry_store import (
    InMemoryInferenceRegistryRepository,
    RegistryStoreError,
)
from app.services.inference_runtime import RuntimeHolder, build_inference_runtime
from app.services.inference_secret_store import InferenceSecretStore
from app.services.inference_secrets import SecretResolutionError, SecretResolver


def _kek(tmp_path, version="v1"):
    path = tmp_path / f"kek-{version}.json"
    path.write_text(
        json.dumps({"key_version": version, "key_b64": base64.b64encode(os.urandom(32)).decode()})
    )
    return InferenceSecretStore.from_files(current_file=str(path))


def _adapter(adapter_id="db-chat"):
    return {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": "https://db.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "db:db-chat"},
        "data_processor": {
            "display_name": "DB chat",
            "purpose": "Inference",
            "data_categories": ["text"],
            "policy_url": "https://db.example.invalid/policy",
        },
    }


def _route(route_id="db-planning", adapter_id="db-chat"):
    return {
        "id": route_id,
        "capability": "planning.chat",
        "adapter_id": adapter_id,
        "model_id": "db-model",
        "model_revision": "2026-01-01",
        "pricing": {"status": "priced", "unit": "tokens", "input_per_million_usd": 0.5},
        "budget": {"feature": "planning"},
    }


# -- CRUD + version counter --------------------------------------------------
def test_version_bumps_on_every_write():
    repo = InMemoryInferenceRegistryRepository()
    assert repo.registry_version() == 0
    repo.upsert_adapter(_adapter())
    assert repo.registry_version() == 1
    repo.upsert_route(_route())
    assert repo.registry_version() == 2
    repo.delete_route("db-planning")
    assert repo.registry_version() == 3


def test_load_document_marks_source_database():
    repo = InMemoryInferenceRegistryRepository()
    repo.upsert_adapter(_adapter())
    repo.upsert_route(_route())
    document, diagnostics = repo.load_document()
    assert document is not None, diagnostics
    assert [r.id for r in document.routes] == ["db-planning"]
    assert all(r.source == "database" for r in document.routes)
    # The offering block round-trips through the store.
    assert document.routes[0].capability.value == "planning.chat"


def test_route_offering_round_trips_through_store():
    repo = InMemoryInferenceRegistryRepository()
    repo.upsert_adapter(_adapter())
    route = _route()
    route["offering"] = {"selectable": True, "labels": ["quality"], "quota_weight": 2.0}
    repo.upsert_route(route)
    document, _ = repo.load_document()
    offering = document.routes[0].offering
    assert offering is not None
    assert offering.selectable is True
    assert offering.quota_weight == 2.0


# -- write-only secrets ------------------------------------------------------
def test_secret_reads_return_metadata_only(tmp_path):
    repo = InMemoryInferenceRegistryRepository(secret_store=_kek(tmp_path))
    meta = repo.put_secret(name="db-chat", scope="global", plaintext="the-key")
    assert meta.name == "db-chat"
    assert meta.fingerprint
    listed = repo.list_secret_metadata()
    assert [m.name for m in listed] == ["db-chat"]
    # Metadata objects never carry a ciphertext or plaintext attribute.
    assert not hasattr(listed[0], "ciphertext")
    assert not hasattr(listed[0], "plaintext")


def test_open_secret_decrypts(tmp_path):
    repo = InMemoryInferenceRegistryRepository(secret_store=_kek(tmp_path))
    repo.put_secret(name="db-chat", scope="global", plaintext="the-key")
    assert repo.open_secret(name="db-chat") == "the-key"


def test_put_secret_bumps_version(tmp_path):
    repo = InMemoryInferenceRegistryRepository(secret_store=_kek(tmp_path))
    before = repo.registry_version()
    repo.put_secret(name="db-chat", scope="global", plaintext="the-key")
    assert repo.registry_version() == before + 1


# -- db: secret resolution through the resolver + redactor -------------------
def _resolver_settings():
    return SimpleNamespace(
        app_env="test", ai_secrets_dir="/run/secrets", ai_secrets_bundle_file=None
    )


def test_db_secret_ref_resolves_through_opener(tmp_path):
    repo = InMemoryInferenceRegistryRepository(secret_store=_kek(tmp_path))
    repo.put_secret(name="db-chat", scope="global", plaintext="resolved-db-secret")
    resolver = SecretResolver(
        settings=_resolver_settings(),
        db_secret_opener=lambda name: repo.open_secret(name=name),
    )
    assert resolver.resolve("db:db-chat") == "resolved-db-secret"


def test_db_secret_ref_without_opener_is_an_error():
    resolver = SecretResolver(settings=_resolver_settings())
    with pytest.raises(SecretResolutionError):
        resolver.resolve("db:anything")


# -- runtime file + database merge; id collision -----------------------------
def _file_settings(tmp_path, document: dict, *, store):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"file-chat": "v", "file-stt": "v"}))
    return SimpleNamespace(
        app_env="test",
        ai_registry_file=str(path),
        ai_registry_database_enabled=True,
        gpu_workers_enabled=True,
        ai_block_noncommercial=False,
        ai_workspace_scoped_routes=False,
        ai_secrets_dir="/run/secrets",
        ai_secrets_bundle_file=str(bundle),
    )


def _file_document():
    return {
        "version": 1,
        "adapters": [
            {
                "id": "file-chat",
                "type": "openai_compatible",
                "base_url": "https://file.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:file-chat"},
                "data_processor": {
                    "display_name": "File chat",
                    "purpose": "Inference",
                    "data_categories": ["text"],
                    "policy_url": "https://file.example.invalid/policy",
                },
            },
            {
                "id": "file-stt",
                "type": "openai_compatible",
                "base_url": "https://stt.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:file-stt"},
                "data_processor": {
                    "display_name": "File stt",
                    "purpose": "Transcription",
                    "data_categories": ["audio"],
                    "policy_url": "https://stt.example.invalid/policy",
                },
            },
        ],
        "models": [],
        "routes": [
            {
                "id": "file-planning",
                "capability": "planning.chat",
                "adapter_id": "file-chat",
                "model_id": "file-model",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "unit": "tokens", "input_per_million_usd": 0.5},
                "budget": {"feature": "planning"},
            },
            {
                "id": "file-stt-route",
                "capability": "transcription.speech_to_text",
                "adapter_id": "file-stt",
                "model_id": "file-stt-model",
                "model_revision": "2026-01-02",
                "pricing": {"status": "priced", "unit": "seconds", "per_unit_usd": 0.0001},
                "budget": {"feature": "transcription"},
            },
        ],
    }


def test_runtime_merges_file_and_database_sources(tmp_path):
    repo = InMemoryInferenceRegistryRepository()
    # A database adapter+route with a neutral, non-colliding id.
    db_adapter = _adapter("db-chat")
    db_adapter["auth"] = {"type": "bearer", "secret_ref": "bundle:file-chat"}
    repo.upsert_adapter(db_adapter)
    repo.upsert_route(_route("db-planning", "db-chat"))
    settings = _file_settings(tmp_path, _file_document(), store=repo)
    runtime = build_inference_runtime(settings, registry_store=repo)
    ids = set(runtime.route_records)
    assert "file-planning" in ids
    assert "db-planning" in ids
    assert runtime.route_records["db-planning"].source == "database"


def test_runtime_rejects_file_database_id_collision(tmp_path):
    repo = InMemoryInferenceRegistryRepository()
    # Reuse the file adapter id -> collision error from merge_registry_documents.
    repo.upsert_adapter(
        {
            "id": "file-chat",
            "type": "openai_compatible",
            "base_url": "https://dup.example.invalid/v1",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:file-chat"},
        }
    )
    settings = _file_settings(tmp_path, _file_document(), store=repo)
    with pytest.raises(RuntimeError):
        build_inference_runtime(settings, registry_store=repo)


# -- RuntimeHolder reload + atomic swap --------------------------------------
def test_runtime_holder_reloads_on_version_change(tmp_path):
    repo = InMemoryInferenceRegistryRepository()
    settings = _file_settings(tmp_path, _file_document(), store=repo)

    def build():
        return build_inference_runtime(settings, registry_store=repo)

    holder = RuntimeHolder(build=build, registry_store=repo, reload_interval_s=0.0)
    first = holder.runtime
    assert "db-planning" not in first.route_records

    # Add a DB route; the version counter advances and a reload picks it up.
    db_adapter = _adapter("db-chat")
    db_adapter["auth"] = {"type": "bearer", "secret_ref": "bundle:file-chat"}
    repo.upsert_adapter(db_adapter)
    repo.upsert_route(_route("db-planning", "db-chat"))

    assert holder.maybe_reload() is True
    second = holder.runtime
    assert "db-planning" in second.route_records
    # Atomic swap: a new runtime object replaces the old; the old is untouched.
    assert second is not first
    assert "db-planning" not in first.route_records


def test_runtime_holder_no_reload_without_version_change(tmp_path):
    repo = InMemoryInferenceRegistryRepository()
    settings = _file_settings(tmp_path, _file_document(), store=repo)
    holder = RuntimeHolder(
        build=lambda: build_inference_runtime(settings, registry_store=repo),
        registry_store=repo,
        reload_interval_s=0.0,
    )
    assert holder.maybe_reload() is False


# -- re-encryption CLI -------------------------------------------------------
def test_reencrypt_reseals_under_current_kek(tmp_path):
    prev = tmp_path / "prev.json"
    prev.write_text(
        json.dumps({"key_version": "v1", "key_b64": base64.b64encode(os.urandom(32)).decode()})
    )
    cur = tmp_path / "cur.json"
    cur.write_text(
        json.dumps({"key_version": "v2", "key_b64": base64.b64encode(os.urandom(32)).decode()})
    )
    v1_store = InferenceSecretStore.from_files(current_file=str(prev))
    repo = InMemoryInferenceRegistryRepository(secret_store=v1_store)
    repo.put_secret(name="db-chat", scope="global", plaintext="rotate-me")

    rotated = InferenceSecretStore.from_files(current_file=str(cur), previous_file=str(prev))
    # Rebind the repo's secret store to the rotated one for the reseal write path.
    repo._secret_store = rotated  # noqa: SLF001 - test wiring
    result = reencrypt_secrets(store=repo, secret_store=rotated)
    assert result.resealed == ("db-chat",)
    # The secret now opens under the current (v2) KEK alone.
    current_only = InferenceSecretStore.from_files(current_file=str(cur))
    repo._secret_store = current_only  # noqa: SLF001 - test wiring
    assert repo.open_secret(name="db-chat") == "rotate-me"


def test_reencrypt_skips_current_version(tmp_path):
    store = _kek(tmp_path, "v1")
    repo = InMemoryInferenceRegistryRepository(secret_store=store)
    repo.put_secret(name="db-chat", scope="global", plaintext="value")
    result = reencrypt_secrets(store=repo, secret_store=store)
    assert result.resealed == ()
    assert result.skipped == ("db-chat",)


def test_open_secret_requires_store():
    repo = InMemoryInferenceRegistryRepository()
    with pytest.raises(RegistryStoreError):
        repo.open_secret(name="x")
