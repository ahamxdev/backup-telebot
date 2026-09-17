import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretCipher:
    """Versioned authenticated encryption for secrets persisted by the application."""

    _VERSION = b"\x01"

    def __init__(self, key_b64: str) -> None:
        try:
            key = base64.b64decode(key_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("master encryption key must be valid base64") from exc
        if len(key) != 32:
            raise ValueError("master encryption key must decode to exactly 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        if not plaintext:
            raise ValueError("secret cannot be empty")
        nonce = os.urandom(12)
        return (
            self._VERSION
            + nonce
            + self._cipher.encrypt(nonce, plaintext.encode(), context.encode())
        )

    def decrypt(self, token: bytes, *, context: str) -> str:
        if len(token) < 30 or token[:1] != self._VERSION:
            raise ValueError("invalid encrypted secret")
        try:
            value = self._cipher.decrypt(token[1:13], token[13:], context.encode())
        except InvalidTag as exc:
            raise ValueError("secret authentication failed") from exc
        return value.decode()


def redact_secret(value: str | bytes | None) -> str:
    """Return a stable non-secret representation safe for UI, audit, and logs."""
    return "[not set]" if value is None else "[redacted]"
