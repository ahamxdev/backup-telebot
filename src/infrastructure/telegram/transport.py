import asyncio
import os
import stat
import time
from pathlib import Path
from typing import Any, Protocol

import structlog
from telethon import TelegramClient

from config.settings import Settings

log = structlog.get_logger()


class TelegramClientProtocol(Protocol):
    async def connect(self) -> None: ...
    def is_connected(self) -> bool: ...
    async def is_user_authorized(self) -> bool: ...
    async def send_message(self, entity: int, message: str, **kwargs: Any) -> Any: ...
    async def send_file(self, entity: int, file: object, **kwargs: Any) -> Any: ...


def proxy_from_settings(settings: Settings) -> tuple[Any, ...] | None:
    if not settings.TELEGRAM_PROXY_ENABLED:
        return None
    if settings.TELEGRAM_PROXY_HOST is None or settings.TELEGRAM_PROXY_PORT is None:
        raise ValueError("enabled proxy requires host and port")
    password = (
        settings.TELEGRAM_PROXY_PASSWORD.get_secret_value()
        if settings.TELEGRAM_PROXY_PASSWORD
        else None
    )
    return (
        "socks5",
        settings.TELEGRAM_PROXY_HOST,
        settings.TELEGRAM_PROXY_PORT,
        True,
        settings.TELEGRAM_PROXY_USERNAME,
        password,
    )


def secure_session_path(path: Path) -> None:
    # Telethon creates SQLite session and journal files lazily. Keep the
    # process-wide creation policy private for the lifetime of this service.
    os.umask(0o077)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise ValueError("Telegram session parent must be a real directory") from exc
    try:
        os.fchmod(parent_fd, 0o700)
        try:
            session_fd = os.open(
                path.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            try:
                session_fd = os.open(
                    path.name,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                # A concurrent creator won the race. Open without following
                # links and validate the resulting object below.
                try:
                    session_fd = os.open(
                        path.name,
                        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=parent_fd,
                    )
                except OSError as exc:
                    raise ValueError("Telegram session must be a regular file") from exc
            except OSError as exc:
                raise ValueError("Telegram session must be a regular file") from exc
        except OSError as exc:
            raise ValueError("Telegram session must be a regular file") from exc
        try:
            state = os.fstat(session_fd)
            if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                raise ValueError("Telegram session must be a regular file")
            os.fchmod(session_fd, 0o600)
        finally:
            os.close(session_fd)
    finally:
        os.close(parent_fd)


def session_permissions_secure(path: Path) -> bool:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return True
    return stat.S_ISREG(state.st_mode) and stat.S_IMODE(state.st_mode) == 0o600


def build_client(settings: Settings) -> TelegramClient:
    api_id, api_hash, _ = settings.require_telegram()
    secure_session_path(settings.TELEGRAM_SESSION_PATH)
    return TelegramClient(
        str(settings.TELEGRAM_SESSION_PATH),
        api_id,
        api_hash,
        proxy=proxy_from_settings(settings),
    )


class TelegramTransport:
    def __init__(self, client: TelegramClientProtocol) -> None:
        self.client = client

    async def send_text(self, telegram_id: int, text: str, *, buttons: Any = None) -> None:
        # Translation substitutions are escaped before reaching this boundary.
        await self.client.send_message(telegram_id, text, buttons=buttons, parse_mode="html")


def _safe_exception_message(exc: Exception) -> str:
    return " ".join(str(exc).split())[:2000]


class TelegramUploadTransport:
    """Connection-aware Telethon transport used by the backup worker."""

    def __init__(self, client: TelegramClientProtocol, progress_interval: int = 5) -> None:
        self.client = client
        self.progress_interval = progress_interval
        self._connect_lock = asyncio.Lock()

    async def _ensure_ready(self) -> None:
        connected = self.client.is_connected()
        log.info("telegram_client_connection_checked", connected=connected)
        if not connected:
            async with self._connect_lock:
                connected = self.client.is_connected()
                log.info("telegram_client_connection_rechecked", connected=connected)
                if not connected:
                    log.info("telegram_client_connect_started")
                    try:
                        await self.client.connect()
                    except Exception as exc:
                        log.exception(
                            "telegram_client_connect_failed",
                            exception_class=type(exc).__name__,
                            exception_message=_safe_exception_message(exc),
                        )
                        raise
                    connected = self.client.is_connected()
                    log.info("telegram_client_connect_completed", connected=connected)
                    if not connected:
                        raise ConnectionError("Telegram client did not reconnect")
        try:
            authorized = await self.client.is_user_authorized()
        except Exception as exc:
            log.exception(
                "telegram_session_verification_failed",
                exception_class=type(exc).__name__,
                exception_message=_safe_exception_message(exc),
            )
            raise
        log.info("telegram_session_verified", authorized=authorized)
        if not authorized:
            raise ConnectionError("Telegram worker session is not authorized")

    async def send_file(self, entity: int, file: object, **kwargs: Any) -> Any:
        await self._ensure_ready()
        original_progress = kwargs.get("progress_callback")
        last_report = 0.0

        def progress(sent: int, total: int) -> None:
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= self.progress_interval or sent >= total:
                last_report = now
                log.info(
                    "telegram_upload_progress",
                    bytes_sent=sent,
                    bytes_total=total,
                )
            if original_progress is not None:
                original_progress(sent, total)

        kwargs["progress_callback"] = progress
        log.info("telegram_send_file_started", file_kind=type(file).__name__)
        try:
            message = await self.client.send_file(entity, file, **kwargs)
        except Exception as exc:
            log.exception(
                "telegram_send_file_failed",
                exception_class=type(exc).__name__,
                exception_message=_safe_exception_message(exc),
            )
            raise
        log.info(
            "telegram_send_file_succeeded",
            message_id=getattr(message, "id", None),
        )
        return message
