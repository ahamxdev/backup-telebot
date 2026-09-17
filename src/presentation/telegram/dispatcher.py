import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.remote_server_service import RemoteServerService
from config.settings import Settings
from infrastructure.telegram import TelegramTransport
from presentation.telegram.constants import (
    AGE_UNLOCK,
    COMPRESSION_ACTIONS,
    DATABASE_CREDENTIAL,
    DISPLAYABLE_RUN_ERROR_CODES,
    ENGINE_ACTIONS,
    PAGE_SIZE,
    PRESET_ACTIONS,
    RUN_PAGE_SIZE,
    SCHEDULE_ACTIONS,
    SOURCE_ACTIONS,
    SSH_IDENTITY,
    SSH_UNLOCK,
    SUPERADMIN_ACTIONS,
    TLS_ACTIONS,
)
from presentation.telegram.routing import RoutingMixin
from presentation.telegram.runtime import CallbackStore, FSMStore, RateLimiter
from security import SecretCipher

_DISPLAYABLE_RUN_ERROR_CODES = DISPLAYABLE_RUN_ERROR_CODES
_DATABASE_CREDENTIAL = DATABASE_CREDENTIAL
_SSH_IDENTITY = SSH_IDENTITY
_SSH_UNLOCK = SSH_UNLOCK
_AGE_UNLOCK = AGE_UNLOCK

__all__ = [
    "COMPRESSION_ACTIONS",
    "ENGINE_ACTIONS",
    "PAGE_SIZE",
    "PRESET_ACTIONS",
    "RUN_PAGE_SIZE",
    "RemoteServerService",
    "SCHEDULE_ACTIONS",
    "SOURCE_ACTIONS",
    "SUPERADMIN_ACTIONS",
    "TLS_ACTIONS",
    "TelegramDispatcher",
    "_AGE_UNLOCK",
    "_DATABASE_CREDENTIAL",
    "_DISPLAYABLE_RUN_ERROR_CODES",
    "_SSH_IDENTITY",
    "_SSH_UNLOCK",
]


class TelegramDispatcher(RoutingMixin):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        transport: TelegramTransport,
        settings: Settings,
    ) -> None:
        self.sessions, self.transport, self.settings = sessions, transport, settings
        self.fsm = FSMStore(settings.FSM_TIMEOUT_SECONDS, settings.INVALID_INPUT_LIMIT)
        self.callbacks = CallbackStore(settings.FSM_TIMEOUT_SECONDS)
        key = settings.MASTER_ENCRYPTION_KEY_B64
        self.cipher = SecretCipher(key.get_secret_value()) if key is not None else None
        self.rate_limit = RateLimiter(
            settings.USER_RATE_LIMIT_PER_MINUTE, settings.GLOBAL_RATE_LIMIT_PER_MINUTE
        )

    async def prune_expired_state(self) -> tuple[int, int]:
        removed = await self.fsm.prune_expired(), await self.callbacks.prune_expired()
        await self.rate_limit.prune_expired()
        return removed

    async def clear_transient_state(self) -> None:
        await self.fsm.clear_all()
        await self.callbacks.clear_all()
        await self.rate_limit.clear_all()

    async def run_state_cleanup(self) -> None:
        interval = max(1.0, min(60.0, self.settings.FSM_TIMEOUT_SECONDS / 2))
        while True:
            await asyncio.sleep(interval)
            await self.prune_expired_state()
