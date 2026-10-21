"""Account details accepted at sign-up (and, from Task 12, at password reset).

Auth enforces only a minimum length (GOTRUE_PASSWORD_MIN_LENGTH); the API
refuses the passwords people guess first, before Auth is called.
"""

from __future__ import annotations

import re

PASSWORD_MIN_LENGTH = 8
# bcrypt, which Auth uses, ignores everything after the first 72 bytes.
PASSWORD_MAX_BYTES = 72
PASSWORD_MIN_DISTINCT_CHARACTERS = 4
EMAIL_MAX_LENGTH = 254
FULL_NAME_MAX_LENGTH = 120

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# The most common passwords of eight or more characters in public breach
# corpora. Compared case-insensitively against the whole password.
COMMON_PASSWORDS = frozenset(
    {
        "00000000",
        "11111111",
        "12341234",
        "12344321",
        "12345678",
        "123456789",
        "1234567890",
        "123123123",
        "1q2w3e4r",
        "1q2w3e4r5t",
        "1qaz2wsx",
        "87654321",
        "88888888",
        "987654321",
        "abc12345",
        "abcd1234",
        "abcdefgh",
        "access14",
        "admin123",
        "asdfasdf",
        "asdfghjk",
        "asdfghjkl",
        "baseball",
        "basketball",
        "batman123",
        "charlie1",
        "computer",
        "corvette",
        "dragon12",
        "football",
        "football1",
        "freedom1",
        "iloveyou",
        "iloveyou1",
        "jennifer",
        "jordan23",
        "letmein1",
        "liverpool",
        "master12",
        "michelle",
        "mustang1",
        "password",
        "password1",
        "password12",
        "password123",
        "passw0rd",
        "princess",
        "qazwsxedc",
        "qwerty12",
        "qwerty123",
        "qwertyui",
        "qwertyuiop",
        "starwars",
        "sunshine",
        "superman",
        "trustno1",
        "welcome1",
        "whatever",
        "zaq12wsx",
        "motionly",
        "motionly1",
        "motionly123",
    }
)


def normalize_email(value: str) -> str | None:
    """The email in the form Auth stores it, or None if it is not an address."""
    email = value.strip().lower()
    if len(email) > EMAIL_MAX_LENGTH or not _EMAIL.fullmatch(email):
        return None
    return email


def normalize_full_name(value: str) -> str | None:
    name = " ".join(value.split())
    if not name or len(name) > FULL_NAME_MAX_LENGTH:
        return None
    return name


def password_problem(password: str, *, email: str) -> str | None:
    """Why a password is refused, or None when it is acceptable."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return "too_short"
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return "too_long"
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        return "common"
    if len(set(lowered)) < PASSWORD_MIN_DISTINCT_CHARACTERS:
        return "too_simple"
    local_part = email.split("@", 1)[0].lower()
    if len(local_part) >= 3 and local_part in lowered:
        return "contains_email"
    return None


PASSWORD_PROBLEM_MESSAGES = {
    "too_short": f"Use at least {PASSWORD_MIN_LENGTH} characters.",
    "too_long": f"Use at most {PASSWORD_MAX_BYTES} bytes.",
    "common": "That password is too common. Choose another.",
    "too_simple": "Use more different characters.",
    "contains_email": "Don't use your email address in your password.",
    "rejected_by_auth": "Choose a stronger password.",
}
