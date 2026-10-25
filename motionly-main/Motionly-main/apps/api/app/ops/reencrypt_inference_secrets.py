"""Re-encrypt database inference secrets under the current KEK (plan Task 20).

    python -m app.ops.reencrypt_inference_secrets [--dry-run]

Key rotation issues a new ``AI_SECRETS_KEK_FILE`` with a fresh ``key_version``
and sets ``AI_SECRETS_KEK_PREVIOUS_FILE`` to the prior KEK for a bounded window.
This command opens every ``ai_secrets`` ciphertext (with whichever loaded KEK
matches its recorded ``key_version``) and re-seals it under the current KEK, so
the previous KEK can then be retired. Rows already sealed under the current key
version are skipped.

It never prints a plaintext secret, a ciphertext or a KEK: output is counts and
secret *names* only. ``--dry-run`` reports what would change without writing.

This is an operator/break-glass tool. It is not imported by the API startup or
the workers, so reading ``ai_secrets`` here never widens the restricted worker
role (apps/api/tests/test_worker_db_grants.py).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from app.services.inference_secret_store import InferenceSecretStore


@dataclass(frozen=True)
class ReencryptResult:
    examined: int
    resealed: tuple[str, ...]
    skipped: tuple[str, ...]


def _normalise_row(row: dict) -> dict:
    """Accept both the in-memory (``*_b64``) and PostgREST (``ciphertext``) shapes."""
    return {
        "name": row["name"],
        "scope": row.get("scope", "global"),
        "ciphertext_b64": row.get("ciphertext_b64", row.get("ciphertext")),
        "nonce_b64": row.get("nonce_b64", row.get("nonce")),
        "key_version": row["key_version"],
    }


def reencrypt_secrets(
    *, store: object, secret_store: InferenceSecretStore, dry_run: bool = False
) -> ReencryptResult:
    """Re-seal every secret in ``store`` under ``secret_store``'s current KEK.

    ``store`` must expose ``secret_rows_for_reencrypt()`` and, for a real write,
    ``store_resealed_secret(name=, scope=, sealed=)``.
    """
    rows = [_normalise_row(row) for row in store.secret_rows_for_reencrypt()]
    resealed: list[str] = []
    skipped: list[str] = []
    for row in rows:
        if row["key_version"] == secret_store.current_key_version:
            skipped.append(row["name"])
            continue
        sealed = secret_store.reseal(
            name=row["name"],
            scope=row["scope"],
            ciphertext_b64=row["ciphertext_b64"],
            nonce_b64=row["nonce_b64"],
            key_version=row["key_version"],
        )
        if sealed is None:  # pragma: no cover - already current, handled above
            skipped.append(row["name"])
            continue
        if not dry_run:
            store.store_resealed_secret(name=row["name"], scope=row["scope"], sealed=sealed)
        resealed.append(row["name"])
    return ReencryptResult(
        examined=len(rows), resealed=tuple(resealed), skipped=tuple(skipped)
    )


def _build_from_settings():
    from app.core.config import get_settings
    from app.services.inference_registry_store import (
        SupabaseInferenceRegistryRepository,
    )

    settings = get_settings()
    secret_store = InferenceSecretStore.from_settings(settings)
    if secret_store is None:
        raise SystemExit("AI_SECRETS_KEK_FILE is not configured; nothing to re-encrypt")
    supabase_url = getattr(settings, "supabase_url", None)
    service_role_key = getattr(settings, "supabase_service_role_key", None)
    if not supabase_url or not service_role_key:
        raise SystemExit("Supabase is not configured; the database secret store is unavailable")
    store = SupabaseInferenceRegistryRepository(
        supabase_url=str(supabase_url),
        service_role_key=str(service_role_key),
        secret_store=secret_store,
    )
    return store, secret_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report changes without writing"
    )
    args = parser.parse_args(argv)
    store, secret_store = _build_from_settings()
    result = reencrypt_secrets(store=store, secret_store=secret_store, dry_run=args.dry_run)
    verb = "would re-encrypt" if args.dry_run else "re-encrypted"
    print(
        f"examined {result.examined} secret(s); {verb} {len(result.resealed)}; "
        f"skipped {len(result.skipped)} already at the current key version"
    )
    for name in result.resealed:
        print(f"  {verb}: {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
