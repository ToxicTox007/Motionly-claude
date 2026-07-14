#!/usr/bin/env python3
"""Encrypt legacy plaintext publishing tokens as an explicit maintenance task."""

from app.core.config import get_settings
from app.repositories.publishing import SupabasePublishingRepository
from app.services.token_cipher import TokenCipher


def main() -> int:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    if not settings.publish_token_encryption_key:
        raise SystemExit("PUBLISH_TOKEN_ENCRYPTION_KEY is required")

    repository = SupabasePublishingRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
        token_cipher=TokenCipher(settings.publish_token_encryption_key),
    )
    migrated = repository.encrypt_legacy_tokens()
    print(f"Encrypted {migrated} legacy publishing connection(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
