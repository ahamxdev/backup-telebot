import asyncio
from contextlib import suppress
from typing import Any

import structlog
from telethon import events

from application.bootstrap_service import BootstrapService
from application.health_service import touch_heartbeats, validate_runtime_schema
from application.run_config import snapshot_legacy_active_runs
from config.settings import Settings
from database.session import Database
from infrastructure.repositories import AuditRepository
from infrastructure.telegram import (
    TelegramTransport,
    build_client,
    secure_session_path,
)
from presentation.telegram.dispatcher import TelegramDispatcher
from security import SecretCipher

log = structlog.get_logger()


async def _record_bot_heartbeat(database: Database, settings: Settings, client: Any) -> None:
    while True:
        if client.is_connected():
            async with database.session_factory() as session:
                await touch_heartbeats(session, ("telegram_bot",))
        await asyncio.sleep(settings.HEALTH_HEARTBEAT_INTERVAL_SECONDS)


async def run_bot(settings: Settings) -> None:
    _, _, token = settings.require_telegram()
    key = settings.MASTER_ENCRYPTION_KEY_B64
    if key is None:
        raise ValueError("bot requires MASTER_ENCRYPTION_KEY_B64")
    SecretCipher(key.get_secret_value())
    superadmin_ids = settings.superadmin_telegram_ids()
    if not superadmin_ids:
        raise ValueError("SUPERADMIN_TELEGRAM_IDS or TELEGRAM_SUPERADMIN_ID is required")
    settings.DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_session_path(settings.TELEGRAM_SESSION_PATH)
    database = Database(settings.DATABASE_URL)
    try:
        if settings.APP_ENV == "production":
            await database.verify_postgresql_ready()
        async with database.session_factory() as session:
            await validate_runtime_schema(session)
            await snapshot_legacy_active_runs(session)
            bootstrap = BootstrapService(session)
            for telegram_id in superadmin_ids:
                await bootstrap.bootstrap_superadmin(
                    telegram_id, settings.TELEGRAM_SUPERADMIN_LANGUAGE
                )
            await AuditRepository(session).record(
                "application_startup",
                details={"proxy_enabled": settings.TELEGRAM_PROXY_ENABLED, "mode": "MTProto bot"},
            )
            await AuditRepository(session).record(
                "proxy_mode", details={"socks5_enabled": settings.TELEGRAM_PROXY_ENABLED}
            )
            await session.commit()
    except Exception:
        await database.dispose()
        raise
    client = build_client(settings)
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)

    async def on_message(event: events.NewMessage.Event) -> None:
        await dispatcher.handle(event)

    async def on_callback(event: events.CallbackQuery.Event) -> None:
        await dispatcher.handle(event)

    client.add_event_handler(on_message, events.NewMessage(incoming=True))
    client.add_event_handler(on_callback, events.CallbackQuery())
    state_cleanup: asyncio.Task[None] | None = None
    runtime_heartbeat: asyncio.Task[None] | None = None
    try:
        await client.start(bot_token=token)
        secure_session_path(settings.TELEGRAM_SESSION_PATH)
        async with database.session_factory() as session:
            await AuditRepository(session).record(
                "telegram_connection_state", details={"connected": True}
            )
            await session.commit()
        log.info("bot_started", proxy_enabled=settings.TELEGRAM_PROXY_ENABLED)
        state_cleanup = asyncio.create_task(dispatcher.run_state_cleanup())
        runtime_heartbeat = asyncio.create_task(_record_bot_heartbeat(database, settings, client))
        await client.run_until_disconnected()
    finally:
        if runtime_heartbeat is not None:
            runtime_heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await runtime_heartbeat
        if state_cleanup is not None:
            state_cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await state_cleanup
        try:
            await dispatcher.clear_transient_state()
        finally:
            try:
                await client.disconnect()
            finally:
                await database.dispose()
