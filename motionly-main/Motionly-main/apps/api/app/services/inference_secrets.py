"""Secret-reference resolution for the declarative inference registry.

Plan Task 5, Section 2.4 ("Secret rules") and invariant 5 ("A remote route has a
secret reference, not a secret value. Secret values are encrypted or mounted
privately, never returned to clients and never logged").

A secret *reference* is a short scheme-qualified name, never the value:

* ``file:<name>`` reads ``<AI_SECRETS_DIR>/<name>`` (default ``/run/secrets``).
  The release agent mounts these read-only (``docs/security/secrets.md``). The
  name may not traverse out of the directory, and a symlink that escapes the
  directory is rejected.
* ``bundle:<key>`` reads ``<key>`` out of a JSON object stored at
  ``AI_SECRETS_BUNDLE_FILE`` — one file holding many operator keys.
* ``env:<NAME>`` reads an environment variable. It is rejected in production and
  logged as a warning elsewhere (it is a development convenience only).
* ``db:<name>`` is not available until Task 20 (DB registry with AES-256-GCM).

:class:`SecretResolver` resolves a reference to a :class:`SecretValue` (or a raw
``str`` via :meth:`SecretResolver.resolve`). It caches per reference, strips a
single trailing newline (secret files commonly end in one), rejects empty
values, and — crucially — registers every resolved value with the generic log
redactor (:func:`app.core.logging.register_secret_value`) before returning, so
the value can never appear verbatim in a log line, exception text or repr.

Every error names the *reference*, never the value.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import SECRET_VALUE_MARKER, register_secret_value

_VALID_SCHEMES = ("file", "bundle", "env", "db")


class SecretResolutionError(RuntimeError):
    """A secret reference could not be resolved.

    The message always names the reference and never the secret value.
    """


class SecretValue:
    """A resolved secret whose ``repr``/``str`` never reveals the value.

    Call :meth:`reveal` where the raw credential is genuinely needed (building a
    request header). Everywhere else — logs, exceptions, debuggers — the value
    is masked.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw secret string. Use only to build an outgoing request."""
        return self._value

    def __str__(self) -> str:  # pragma: no cover - trivial
        return SECRET_VALUE_MARKER

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SecretValue({SECRET_VALUE_MARKER})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretValue) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


def parse_secret_ref(ref: str) -> tuple[str, str]:
    """Split ``<scheme>:<rest>`` and validate the scheme. Never echoes a value.

    Returns ``(scheme, rest)``. Raises :class:`SecretResolutionError` for a
    malformed reference (missing scheme, empty remainder, or unknown scheme).
    """
    if not isinstance(ref, str) or ":" not in ref:
        raise SecretResolutionError("secret_ref must look like 'file:<name>' or 'bundle:<key>'")
    scheme, _, rest = ref.partition(":")
    if not rest:
        raise SecretResolutionError(f"secret_ref {ref!r} is missing a name after the scheme")
    if scheme not in _VALID_SCHEMES:
        raise SecretResolutionError(f"unknown secret_ref scheme {scheme!r}")
    return scheme, rest


class SecretResolver:
    """Resolves secret references to raw values, caching and redacting them.

    One resolver is built per runtime (Task 6) with the active settings. It is
    thread-safe: the OAuth strategy and concurrent adapters may resolve the same
    reference at once.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        logger=None,  # noqa: ANN001 - optional logging.Logger
        db_secret_opener=None,  # noqa: ANN001 - Callable[[str], str] | None
    ) -> None:
        self._settings = settings or get_settings()
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._bundle: dict[str, object] | None = None
        self._bundle_loaded = False
        # ``db:<name>`` resolution (plan Task 20). A callable that maps a secret
        # name to its decrypted plaintext (the registry store resolves the row
        # and the secret store decrypts it). ``None`` means database secrets are
        # not configured, so a ``db:`` ref is a clear error.
        self._db_secret_opener = db_secret_opener
        if logger is None:
            from app.core.logging import get_logger

            logger = get_logger(__name__)
        self._logger = logger

    # -- public API --------------------------------------------------------

    def resolve(self, ref: str) -> str:
        """Resolve ``ref`` to its raw secret string (cached, redacted, non-empty).

        Strips one trailing newline, rejects an empty result, and registers the
        value with the log redactor before returning. Errors name ``ref``, never
        the value.
        """
        with self._lock:
            cached = self._cache.get(ref)
        if cached is not None:
            return cached

        scheme, rest = parse_secret_ref(ref)
        raw = self._read(scheme, rest, ref)
        value = raw.rstrip("\n") if raw.endswith("\n") else raw
        if not value:
            raise SecretResolutionError(f"secret_ref {ref!r} resolved to an empty value")

        # Register before returning so the value is masked everywhere, including
        # any later exception that quotes a request the caller built from it.
        register_secret_value(value)
        with self._lock:
            self._cache[ref] = value
        return value

    def resolve_value(self, ref: str) -> SecretValue:
        """Like :meth:`resolve` but wrapped so ``repr``/``str`` stay redacted."""
        return SecretValue(self.resolve(ref))

    def __repr__(self) -> str:
        # Never expose cached values or even reference names (a name can hint at
        # an operator). Only the count is safe.
        return f"SecretResolver(cached={len(self._cache)})"

    # -- scheme handlers ---------------------------------------------------

    def _read(self, scheme: str, rest: str, ref: str) -> str:
        if scheme == "file":
            return self._read_file(rest, ref)
        if scheme == "bundle":
            return self._read_bundle(rest, ref)
        if scheme == "env":
            return self._read_env(rest, ref)
        if scheme == "db":
            return self._read_db(rest, ref)
        raise SecretResolutionError(f"unknown secret_ref scheme {scheme!r}")

    def _read_db(self, name: str, ref: str) -> str:
        """Resolve a ``db:<name>`` ref through the injected opener (plan Task 20).

        The opener decrypts the ``ai_secrets`` row for ``name`` with the
        AES-256-GCM secret store. The value is never logged; errors name the
        reference only.
        """
        if self._db_secret_opener is None:
            raise SecretResolutionError(
                f"db: secret refs require the database secret store "
                f"(set AI_SECRETS_KEK_FILE and enable the DB registry) for ref {ref!r}"
            )
        try:
            value = self._db_secret_opener(name)
        except SecretResolutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak the value/cause detail
            raise SecretResolutionError(
                f"secret_ref {ref!r} could not be resolved from the database"
            ) from exc
        if not isinstance(value, str) or not value:
            raise SecretResolutionError(
                f"secret_ref {ref!r} resolved to an empty database value"
            )
        return value

    def _read_file(self, name: str, ref: str) -> str:
        base = Path(self._settings.ai_secrets_dir).resolve()
        # Reject absolute names and any traversal: the name must stay under base.
        candidate = base / name
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise SecretResolutionError(f"secret_ref {ref!r} could not be resolved") from exc
        if resolved != base and base not in resolved.parents:
            # Path traversal or a symlink that escapes the secrets directory.
            raise SecretResolutionError(f"secret_ref {ref!r} escapes the secrets directory")
        if not resolved.is_file():
            raise SecretResolutionError(f"secret_ref {ref!r} does not name a file")
        try:
            return resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretResolutionError(f"secret_ref {ref!r} could not be read") from exc

    def _read_bundle(self, key: str, ref: str) -> str:
        bundle = self._load_bundle(ref)
        if key not in bundle:
            raise SecretResolutionError(
                f"secret_ref {ref!r} names a key not present in the secret bundle"
            )
        value = bundle[key]
        if not isinstance(value, str):
            raise SecretResolutionError(
                f"secret_ref {ref!r} must name a string value in the secret bundle"
            )
        return value

    def _load_bundle(self, ref: str) -> dict[str, object]:
        with self._lock:
            if self._bundle_loaded:
                if self._bundle is None:
                    raise SecretResolutionError(
                        "no secret bundle is configured (set AI_SECRETS_BUNDLE_FILE) "
                        f"for ref {ref!r}"
                    )
                return self._bundle
        bundle_file = self._settings.ai_secrets_bundle_file
        if not bundle_file:
            with self._lock:
                self._bundle_loaded = True
                self._bundle = None
            raise SecretResolutionError(
                f"no secret bundle is configured (set AI_SECRETS_BUNDLE_FILE) for ref {ref!r}"
            )
        path = Path(bundle_file)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretResolutionError(f"secret bundle for ref {ref!r} could not be read") from exc
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise SecretResolutionError(f"secret bundle for ref {ref!r} is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise SecretResolutionError(f"secret bundle for ref {ref!r} must be a JSON object")
        with self._lock:
            self._bundle = parsed
            self._bundle_loaded = True
        return parsed

    def _read_env(self, name: str, ref: str) -> str:
        if self._settings.app_env == "production":
            raise SecretResolutionError(
                f"env: secret refs are rejected in production (ref {ref!r})"
            )
        self._logger.warning(
            "inference_secret_env_ref_used",
            extra={"secret_ref_scheme": "env"},
        )
        value = os.environ.get(name)
        if value is None:
            raise SecretResolutionError(
                f"secret_ref {ref!r} names an environment variable that is not set"
            )
        return value
