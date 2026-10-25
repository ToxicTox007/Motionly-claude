"""AES-256-GCM encrypted storage for inference secrets (plan Task 20, decision 3a).

The database secret store (``ai_secrets`` table, Task 20 migration) never holds a
plaintext credential. Each secret is sealed with AES-256-GCM under a deployment
**key-encryption key (KEK)** read from a file. The additional-authenticated-data
(AAD) binds the secret's ``name``, ``scope`` and the KEK **key version**, so a
ciphertext cannot be replayed under a different name/scope/key, and a KEK
rotation is detectable.

KEK file format
---------------
A KEK file is a small UTF-8 JSON object::

    {
      "key_version": "2026-10-25",
      "key_b64": "<base64 of exactly 32 raw bytes>"
    }

* ``key_version`` is an opaque, non-secret identifier recorded next to every
  ciphertext and mixed into the AAD. Rotating the KEK means issuing a new file
  with a new ``key_version`` and a new 32-byte key.
* ``key_b64`` is the standard-base64 encoding of exactly 32 bytes (AES-256).

``AI_SECRETS_KEK_FILE`` points at the current KEK. During a bounded rotation
window ``AI_SECRETS_KEK_PREVIOUS_FILE`` points at the prior KEK so the store can
still decrypt ciphertexts sealed under it; the re-encryption CLI
(``app.ops.reencrypt_inference_secrets``) rewrites every ciphertext under the
current KEK and the previous file is then removed.

Fingerprint
-----------
:meth:`InferenceSecretStore.fingerprint` returns a short, non-reversible
identifier derived from the plaintext with HMAC-SHA256 keyed by the KEK (so it
cannot be computed without the KEK and never reveals the value or its length
beyond a fixed-width digest prefix). It lets an operator confirm two secrets are
identical, or that a rotation preserved a value, without exposing the value.

Nothing here logs a plaintext secret, a KEK, or a ciphertext.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEK_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # AES-GCM standard nonce
_FINGERPRINT_PREFIX_LEN = 16  # hex chars of the keyed digest surfaced to operators


class SecretStoreError(RuntimeError):
    """A secret could not be sealed or opened. Never contains a plaintext value."""


@dataclass(frozen=True)
class _Kek:
    key_version: str
    key: bytes  # exactly 32 raw bytes


@dataclass(frozen=True)
class SealedSecret:
    """An encrypted secret ready for the ``ai_secrets`` table (no plaintext)."""

    ciphertext_b64: str
    nonce_b64: str
    key_version: str
    fingerprint: str


def _load_kek_from_text(text: str, source: str) -> _Kek:
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise SecretStoreError(f"KEK file {source!r} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SecretStoreError(f"KEK file {source!r} must be a JSON object")
    key_version = parsed.get("key_version")
    key_b64 = parsed.get("key_b64")
    if not isinstance(key_version, str) or not key_version:
        raise SecretStoreError(f"KEK file {source!r} is missing a string 'key_version'")
    if not isinstance(key_b64, str) or not key_b64:
        raise SecretStoreError(f"KEK file {source!r} is missing a string 'key_b64'")
    try:
        key = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretStoreError(f"KEK file {source!r} key_b64 is not valid base64") from exc
    if len(key) != _KEK_BYTES:
        raise SecretStoreError(
            f"KEK file {source!r} key must be exactly {_KEK_BYTES} bytes (AES-256)"
        )
    return _Kek(key_version=key_version, key=key)


def _load_kek_file(path: str) -> _Kek:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SecretStoreError(f"KEK file {path!r} could not be read") from exc
    return _load_kek_from_text(text, path)


def _aad(name: str, scope: str, key_version: str) -> bytes:
    """Bind name, scope and key version so a ciphertext cannot be relabelled."""
    return b"\x00".join(
        part.encode("utf-8") for part in ("inference-secret", name, scope, key_version)
    )


class InferenceSecretStore:
    """Seal/open secrets with AES-256-GCM under a current (and optional prior) KEK.

    Construct with :meth:`from_settings` in the app, or directly for tests. The
    store is stateless beyond the loaded keys and is safe to share.
    """

    def __init__(self, *, current: _Kek, previous: _Kek | None = None) -> None:
        self._current = current
        self._previous = previous

    # -- construction ------------------------------------------------------
    @classmethod
    def from_files(
        cls, *, current_file: str, previous_file: str | None = None
    ) -> InferenceSecretStore:
        current = _load_kek_file(current_file)
        previous = _load_kek_file(previous_file) if previous_file else None
        if previous is not None and previous.key_version == current.key_version:
            raise SecretStoreError(
                "current and previous KEK share a key_version; rotation needs a new version"
            )
        return cls(current=current, previous=previous)

    @classmethod
    def from_settings(cls, settings: object) -> InferenceSecretStore | None:
        """Build from ``AI_SECRETS_KEK_FILE`` (and optional previous), or ``None``.

        Returns ``None`` when no current KEK file is configured, so callers can
        treat the database secret store as simply unavailable.
        """
        current_file = getattr(settings, "ai_secrets_kek_file", None)
        if not current_file:
            return None
        previous_file = getattr(settings, "ai_secrets_kek_previous_file", None)
        return cls.from_files(current_file=str(current_file), previous_file=previous_file or None)

    # -- public API --------------------------------------------------------
    @property
    def current_key_version(self) -> str:
        return self._current.key_version

    def seal(self, *, name: str, scope: str, plaintext: str) -> SealedSecret:
        """Encrypt ``plaintext`` for ``(name, scope)`` under the current KEK."""
        if not plaintext:
            raise SecretStoreError("cannot seal an empty secret value")
        nonce = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(self._current.key)
        aad = _aad(name, scope, self._current.key_version)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return SealedSecret(
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
            key_version=self._current.key_version,
            fingerprint=self.fingerprint(plaintext),
        )

    def open(
        self,
        *,
        name: str,
        scope: str,
        ciphertext_b64: str,
        nonce_b64: str,
        key_version: str,
    ) -> str:
        """Decrypt a sealed secret. Tries the KEK whose version matches, falling
        back to the previous KEK during a rotation window. Errors never reveal
        the value."""
        kek = self._kek_for_version(key_version)
        try:
            nonce = base64.b64decode(nonce_b64, validate=True)
            ciphertext = base64.b64decode(ciphertext_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretStoreError(f"secret {name!r} has malformed ciphertext") from exc
        aesgcm = AESGCM(kek.key)
        aad = _aad(name, scope, key_version)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise SecretStoreError(
                f"secret {name!r} failed authentication (wrong KEK, name, scope or "
                "key version)"
            ) from exc
        return plaintext.decode("utf-8")

    def fingerprint(self, plaintext: str) -> str:
        """A short, non-reversible identifier keyed by the current KEK.

        HMAC-SHA256 keyed by the KEK, truncated to a fixed width. It reveals
        nothing about the value or its length and cannot be computed without the
        KEK, yet two equal values under the same KEK produce the same
        fingerprint (useful to confirm a rotation preserved a value).
        """
        digest = hmac.new(self._current.key, plaintext.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()[:_FINGERPRINT_PREFIX_LEN]

    def reseal(
        self,
        *,
        name: str,
        scope: str,
        ciphertext_b64: str,
        nonce_b64: str,
        key_version: str,
    ) -> SealedSecret | None:
        """Re-encrypt a secret under the current KEK (plan Task 20 rotation CLI).

        Returns ``None`` when the secret is already sealed under the current key
        version (nothing to do); otherwise opens it with the matching KEK and
        seals it afresh under the current KEK.
        """
        if key_version == self._current.key_version:
            return None
        plaintext = self.open(
            name=name,
            scope=scope,
            ciphertext_b64=ciphertext_b64,
            nonce_b64=nonce_b64,
            key_version=key_version,
        )
        return self.seal(name=name, scope=scope, plaintext=plaintext)

    # -- internals ---------------------------------------------------------
    def _kek_for_version(self, key_version: str) -> _Kek:
        if key_version == self._current.key_version:
            return self._current
        if self._previous is not None and key_version == self._previous.key_version:
            return self._previous
        raise SecretStoreError(
            f"no KEK is loaded for key_version {key_version!r}; configure "
            "AI_SECRETS_KEK_PREVIOUS_FILE during rotation"
        )


__all__ = [
    "InferenceSecretStore",
    "SealedSecret",
    "SecretStoreError",
]
