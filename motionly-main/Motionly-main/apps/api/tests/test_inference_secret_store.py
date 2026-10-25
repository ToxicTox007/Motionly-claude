"""Tests for the AES-256-GCM inference secret store (plan Task 20).

Covers the KEK file format, an encryption round-trip, wrong-AAD rejection
(name/scope/key version), KEK rotation with a previous key, the re-seal helper,
and the non-reversible fingerprint.
"""

from __future__ import annotations

import base64
import json
import os

import pytest

from app.services.inference_secret_store import (
    InferenceSecretStore,
    SecretStoreError,
)


def _kek_file(tmp_path, name: str, version: str, key: bytes | None = None) -> str:
    key = key or os.urandom(32)
    path = tmp_path / name
    path.write_text(
        json.dumps({"key_version": version, "key_b64": base64.b64encode(key).decode()}),
        encoding="utf-8",
    )
    return str(path)


def test_round_trip(tmp_path):
    store = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "k.json", "v1"))
    sealed = store.seal(name="chat", scope="global", plaintext="s3cret-value")
    assert sealed.key_version == "v1"
    opened = store.open(
        name="chat",
        scope="global",
        ciphertext_b64=sealed.ciphertext_b64,
        nonce_b64=sealed.nonce_b64,
        key_version=sealed.key_version,
    )
    assert opened == "s3cret-value"


def test_ciphertext_does_not_contain_plaintext(tmp_path):
    store = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "k.json", "v1"))
    sealed = store.seal(name="chat", scope="global", plaintext="plain-marker-xyz")
    assert "plain-marker-xyz" not in sealed.ciphertext_b64
    assert "plain-marker-xyz" not in sealed.fingerprint


@pytest.mark.parametrize(
    "wrong",
    [
        {"name": "other"},
        {"scope": "workspace:1"},
        {"key_version": "v2"},
    ],
)
def test_wrong_aad_is_rejected(tmp_path, wrong):
    store = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "k.json", "v1"))
    sealed = store.seal(name="chat", scope="global", plaintext="value")
    kwargs = {
        "name": "chat",
        "scope": "global",
        "ciphertext_b64": sealed.ciphertext_b64,
        "nonce_b64": sealed.nonce_b64,
        "key_version": sealed.key_version,
    }
    kwargs.update(wrong)
    with pytest.raises(SecretStoreError):
        store.open(**kwargs)


def test_wrong_kek_is_rejected(tmp_path):
    store_a = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "a.json", "v1"))
    store_b = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "b.json", "v1"))
    sealed = store_a.seal(name="chat", scope="global", plaintext="value")
    with pytest.raises(SecretStoreError):
        store_b.open(
            name="chat",
            scope="global",
            ciphertext_b64=sealed.ciphertext_b64,
            nonce_b64=sealed.nonce_b64,
            key_version="v1",
        )


def test_rotation_opens_previous_and_reseals(tmp_path):
    current = _kek_file(tmp_path, "current.json", "v2")
    previous = _kek_file(tmp_path, "previous.json", "v1")
    # Seal under v1 (previous) then rotate.
    v1_store = InferenceSecretStore.from_files(current_file=previous)
    sealed_v1 = v1_store.seal(name="chat", scope="global", plaintext="rotate-me")

    rotated = InferenceSecretStore.from_files(current_file=current, previous_file=previous)
    # The rotated store can still open the v1 ciphertext via the previous KEK.
    assert (
        rotated.open(
            name="chat",
            scope="global",
            ciphertext_b64=sealed_v1.ciphertext_b64,
            nonce_b64=sealed_v1.nonce_b64,
            key_version="v1",
        )
        == "rotate-me"
    )
    resealed = rotated.reseal(
        name="chat",
        scope="global",
        ciphertext_b64=sealed_v1.ciphertext_b64,
        nonce_b64=sealed_v1.nonce_b64,
        key_version="v1",
    )
    assert resealed is not None
    assert resealed.key_version == "v2"
    # The resealed ciphertext opens under the current KEK alone.
    current_only = InferenceSecretStore.from_files(current_file=current)
    assert (
        current_only.open(
            name="chat",
            scope="global",
            ciphertext_b64=resealed.ciphertext_b64,
            nonce_b64=resealed.nonce_b64,
            key_version="v2",
        )
        == "rotate-me"
    )


def test_reseal_skips_current_version(tmp_path):
    store = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "k.json", "v1"))
    sealed = store.seal(name="chat", scope="global", plaintext="value")
    assert (
        store.reseal(
            name="chat",
            scope="global",
            ciphertext_b64=sealed.ciphertext_b64,
            nonce_b64=sealed.nonce_b64,
            key_version="v1",
        )
        is None
    )


def test_fingerprint_is_stable_and_non_reversible(tmp_path):
    store = InferenceSecretStore.from_files(current_file=_kek_file(tmp_path, "k.json", "v1"))
    fp1 = store.fingerprint("the-value")
    fp2 = store.fingerprint("the-value")
    assert fp1 == fp2
    assert "the-value" not in fp1
    assert store.fingerprint("different") != fp1
    # Fixed width, hex, independent of input length.
    assert len(store.fingerprint("x")) == len(store.fingerprint("x" * 200))


def test_bad_kek_file_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"key_version": "v1", "key_b64": base64.b64encode(b"short").decode()})
    )
    with pytest.raises(SecretStoreError):
        InferenceSecretStore.from_files(current_file=str(bad))


def test_rotation_requires_distinct_versions(tmp_path):
    same = _kek_file(tmp_path, "a.json", "v1")
    other = _kek_file(tmp_path, "b.json", "v1")
    with pytest.raises(SecretStoreError):
        InferenceSecretStore.from_files(current_file=same, previous_file=other)


def test_from_settings_returns_none_without_kek():
    from types import SimpleNamespace

    assert InferenceSecretStore.from_settings(SimpleNamespace(ai_secrets_kek_file=None)) is None
