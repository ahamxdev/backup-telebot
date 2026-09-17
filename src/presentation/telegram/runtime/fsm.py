import asyncio
import os
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class State(StrEnum):
    EDIT_BACKUP_TIMEZONE = "edit_backup_timezone"
    ADD_SERVER_NAME = "add_server_name"
    ADD_SERVER_HOST = "add_server_host"
    ADD_SERVER_PORT = "add_server_port"
    ADD_SERVER_USERNAME = "add_server_username"
    ADD_SERVER_PRIVATE_KEY = "add_server_private_key"
    ADD_SERVER_PASSPHRASE = "add_server_" + "passphrase"
    CONFIRM_SERVER_FINGERPRINT = "confirm_server_fingerprint"
    SELECT_SERVER_EDIT = "select_server_edit"
    EDIT_SERVER_ACTION = "edit_server_action"
    SELECT_SERVER_DELETE = "select_server_delete"
    CONFIRM_SERVER_DELETE = "confirm_server_delete"
    ADD_BACKUP_NAME = "add_backup_name"
    ADD_BACKUP_DESCRIPTION = "add_backup_description"
    ADD_BACKUP_SOURCE = "add_backup_source"
    ADD_BACKUP_DATABASE_ENGINE = "add_backup_database_engine"
    ADD_BACKUP_DATABASE_HOST = "add_backup_database_host"
    ADD_BACKUP_DATABASE_PORT = "add_backup_database_port"
    ADD_BACKUP_DATABASE_NAME = "add_backup_database_name"
    ADD_BACKUP_DATABASE_USERNAME = "add_backup_database_username"
    ADD_BACKUP_DATABASE_PASSWORD = "add_backup_database_" + "password"
    ADD_BACKUP_DATABASE_TLS = "add_backup_database_tls"
    ADD_BACKUP_DATABASE_TLS_CA = "add_backup_database_tls_ca"
    ADD_BACKUP_MSSQL_DOCKER = "add_backup_mssql_docker"
    ADD_BACKUP_MSSQL_PATH = "add_backup_mssql_path"
    ADD_BACKUP_MSSQL_HOST_PATH = "add_backup_mssql_host_path"
    ADD_BACKUP_MSSQL_SERVER = "add_backup_mssql_server"
    ADD_BACKUP_FILES_LOCATION = "add_backup_files_location"
    ADD_BACKUP_FILES_SERVER = "add_backup_files_server"
    ADD_BACKUP_FILES_PATHS = "add_backup_files_paths"
    ADD_BACKUP_SCHEDULE = "add_backup_schedule"
    ADD_BACKUP_SCHEDULE_VALUE = "add_backup_schedule_value"
    ADD_BACKUP_ARCHIVE = "add_backup_archive"
    ADD_BACKUP_COMPRESSION = "add_backup_compression"
    ADD_BACKUP_COMPRESSION_PRESET = "add_backup_compression_preset"
    ADD_BACKUP_ENCRYPTION = "add_backup_encryption"
    ADD_BACKUP_ENCRYPTION_SECRET = "add_backup_encryption_" + "secret"
    ADD_BACKUP_ADMINS = "add_backup_admins"
    ADD_BACKUP_CONFIRM = "add_backup_confirm"
    ADD_ADMIN_NAME = "add_admin_name"
    ADD_ADMIN_ID = "add_admin_id"
    ADD_ADMIN_LANGUAGE = "add_admin_language"
    ADD_ADMIN_CONFIRM = "add_admin_confirm"
    ADD_ADMIN_ACCESS = "add_admin_access"
    SELECT_BACKUP_EDIT = "select_backup_edit"
    EDIT_BACKUP_ACTION = "edit_backup_action"
    EDIT_BACKUP_NAME = "edit_backup_name"
    EDIT_BACKUP_DESCRIPTION = "edit_backup_description"
    SELECT_BACKUP_DELETE = "select_backup_delete"
    CONFIRM_BACKUP_DELETE = "confirm_backup_delete"
    BACKUP_ADMIN_ACCESS = "backup_admin_access"
    SELECT_ADMIN_EDIT = "select_admin_edit"
    EDIT_ADMIN_ACTION = "edit_admin_action"
    EDIT_ADMIN_NAME = "edit_admin_name"
    EDIT_ADMIN_TELEGRAM_ID = "edit_admin_telegram_id"
    EDIT_ADMIN_LANGUAGE = "edit_admin_language"
    EDIT_ADMIN_ACCESS = "edit_admin_access"
    SELECT_ADMIN_DELETE = "select_admin_delete"
    CONFIRM_ADMIN_DELETE = "confirm_admin_delete"


@dataclass
class Conversation:
    state: State
    expires_at: float
    data: dict[str, Any] = field(default_factory=dict)
    option_ids: dict[str, int] = field(default_factory=dict)
    invalid_inputs: int = 0
    history: list[State] = field(default_factory=list)


@dataclass
class _EncryptedSecret:
    nonce: bytes
    ciphertext: bytearray
    expires_at: float

    def erase(self) -> None:
        self.ciphertext[:] = b"\x00" * len(self.ciphertext)


class EphemeralSecretStore:
    """Process-local encrypted storage for secrets used by active conversations."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout = timeout_seconds
        self._cipher = AESGCM(AESGCM.generate_key(bit_length=256))
        self._items: dict[tuple[int, str], _EncryptedSecret] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _context(user_id: int, name: str) -> bytes:
        return f"fsm-secret-v1:{user_id}:{name}".encode()

    async def put(self, user_id: int, name: str, plaintext: str) -> None:
        if not plaintext:
            raise ValueError("secret cannot be empty")
        nonce = os.urandom(12)
        ciphertext = bytearray(
            self._cipher.encrypt(nonce, plaintext.encode(), self._context(user_id, name))
        )
        async with self._lock:
            previous = self._items.pop((user_id, name), None)
            if previous is not None:
                previous.erase()
            self._items[(user_id, name)] = _EncryptedSecret(
                nonce, ciphertext, monotonic() + self.timeout
            )

    async def take(self, user_id: int, name: str) -> str:
        async with self._lock:
            item = self._items.pop((user_id, name), None)
            if item is None or item.expires_at <= monotonic():
                if item is not None:
                    item.erase()
                raise ValueError("conversation secret is unavailable or expired")
            try:
                return self._cipher.decrypt(
                    item.nonce,
                    bytes(item.ciphertext),
                    self._context(user_id, name),
                ).decode()
            finally:
                item.erase()

    async def touch(self, user_id: int) -> None:
        async with self._lock:
            expires_at = monotonic() + self.timeout
            for (owner_id, _name), item in self._items.items():
                if owner_id == user_id:
                    item.expires_at = expires_at

    async def clear(self, user_id: int) -> None:
        async with self._lock:
            keys = [key for key in self._items if key[0] == user_id]
            for key in keys:
                self._items.pop(key).erase()

    async def clear_all(self) -> None:
        async with self._lock:
            for item in self._items.values():
                item.erase()
            self._items.clear()

    async def prune_expired(self) -> int:
        async with self._lock:
            now = monotonic()
            keys = [key for key, item in self._items.items() if item.expires_at <= now]
            for key in keys:
                self._items.pop(key).erase()
            return len(keys)

    async def contains(self, user_id: int, name: str | None = None) -> bool:
        async with self._lock:
            now = monotonic()
            return any(
                owner_id == user_id
                and (name is None or item_name == name)
                and item.expires_at > now
                for (owner_id, item_name), item in self._items.items()
            )


class FSMStore:
    def __init__(self, timeout_seconds: int, invalid_limit: int) -> None:
        self.timeout, self.invalid_limit = timeout_seconds, invalid_limit
        self._states: dict[int, Conversation] = {}
        self._lock = asyncio.Lock()
        self.secrets = EphemeralSecretStore(timeout_seconds)

    async def get(self, user_id: int) -> Conversation | None:
        async with self._lock:
            item = self._states.get(user_id)
            if item and item.expires_at <= monotonic():
                self._states.pop(user_id, None)
                await self.secrets.clear(user_id)
                return None
            return item

    async def set(
        self, user_id: int, state: State, data: dict[str, Any] | None = None
    ) -> Conversation:
        async with self._lock:
            await self.secrets.clear(user_id)
            item = Conversation(state, monotonic() + self.timeout, data or {})
            self._states[user_id] = item
            return item

    async def transition(self, user_id: int, expected: State, new: State) -> Conversation:
        async with self._lock:
            item = self._states.get(user_id)
            if item is None or item.state is not expected or item.expires_at <= monotonic():
                if item is not None and item.expires_at <= monotonic():
                    self._states.pop(user_id, None)
                    await self.secrets.clear(user_id)
                raise ValueError("invalid FSM transition")
            item.state, item.expires_at, item.invalid_inputs = new, monotonic() + self.timeout, 0
            item.history.append(expected)
            await self.secrets.touch(user_id)
            return item

    async def back(self, user_id: int) -> Conversation | None:
        async with self._lock:
            item = self._states.get(user_id)
            if item is None or item.expires_at <= monotonic():
                if item is not None:
                    self._states.pop(user_id, None)
                    await self.secrets.clear(user_id)
                return None
            if not item.history:
                return None
            item.state = item.history.pop()
            item.expires_at = monotonic() + self.timeout
            item.invalid_inputs = 0
            await self.secrets.touch(user_id)
            return item

    async def invalid(self, user_id: int) -> bool:
        async with self._lock:
            item = self._states.get(user_id)
            if item is None:
                return False
            item.invalid_inputs += 1
            item.expires_at = monotonic() + self.timeout
            if item.invalid_inputs >= self.invalid_limit:
                self._states.pop(user_id, None)
                await self.secrets.clear(user_id)
                return True
            await self.secrets.touch(user_id)
            return False

    async def clear(self, user_id: int) -> None:
        async with self._lock:
            self._states.pop(user_id, None)
            await self.secrets.clear(user_id)

    async def prune_expired(self) -> int:
        async with self._lock:
            now = monotonic()
            user_ids = [user_id for user_id, item in self._states.items() if item.expires_at <= now]
            for user_id in user_ids:
                self._states.pop(user_id, None)
                await self.secrets.clear(user_id)
        await self.secrets.prune_expired()
        return len(user_ids)

    async def clear_all(self) -> None:
        async with self._lock:
            self._states.clear()
            await self.secrets.clear_all()
