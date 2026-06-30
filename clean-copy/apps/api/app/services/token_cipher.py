import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.api.errors import ApiError


class TokenCipher:
    """Versioned authenticated encryption for provider credentials at rest."""

    PREFIX = "enc:v1:"

    def __init__(self, secret: str) -> None:
        if len(secret.strip()) < 32:
            raise ValueError("PUBLISH_TOKEN_ENCRYPTION_KEY must contain at least 32 characters")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        if not value or value.startswith(self.PREFIX):
            return value
        encrypted = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{self.PREFIX}{encrypted}"

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return value
        if not value.startswith(self.PREFIX):
            return value
        try:
            return self._fernet.decrypt(value.removeprefix(self.PREFIX).encode("ascii")).decode(
                "utf-8"
            )
        except InvalidToken as exc:
            raise ApiError(
                500,
                "publish_token_decryption_failed",
                "A publishing credential could not be decrypted. Reconnect the account.",
            ) from exc

    def is_encrypted(self, value: str | None) -> bool:
        return bool(value and value.startswith(self.PREFIX))
