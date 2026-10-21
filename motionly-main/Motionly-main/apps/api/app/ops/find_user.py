"""Print a user's ID for a given email, via GoTrue's admin API.

    python -m app.ops.find_user someone@example.com

Use this to bootstrap the first admin: run it once, copy the printed UUID into
``ADMIN_USER_IDS`` for the api service, and restart. That account can then open
the admin Users page and help everyone else (Task 12). The script reads
SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from the API's settings and prints
only the id and email; it never issues reset links or touches passwords.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings
from app.services.auth_admin import AuthAdminClient, AuthAdminError
from app.services.signup_policy import normalize_email


async def _find(email: str) -> int:
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.", file=sys.stderr)
        return 2
    normalized = normalize_email(email)
    if normalized is None:
        print(f"Not a valid email address: {email!r}", file=sys.stderr)
        return 2
    client = AuthAdminClient(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        user = await client.find_user_by_email(email=normalized)
    except AuthAdminError as exc:
        print(f"Auth admin error: {exc.code}", file=sys.stderr)
        return 1
    if user is None:
        print(f"No account found for {normalized}", file=sys.stderr)
        return 1
    print(f"{user.id}\t{user.email}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m app.ops.find_user <email>", file=sys.stderr)
        return 2
    return asyncio.run(_find(args[0]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
