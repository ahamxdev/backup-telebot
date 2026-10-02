import asyncio
import fcntl
import os
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

import structlog
from telethon import TelegramClient

from config.settings import Settings
from infrastructure.telegram.errors import (
    DeliveryErrorKind,
    TelegramAuthorizationError,
    TelegramTransportError,
    classify_delivery_error,
)

log = structlog.get_logger()


class TelegramClientProtocol(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    async def is_user_authorized(self) -> bool: ...
    async def sign_in(self, *, bot_token: str) -> Any: ...
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


def build_client(settings: Settings, *, worker: bool = False) -> TelegramClient:
    api_id, api_hash, _ = settings.require_telegram()
    secure_session_path(settings.TELEGRAM_SESSION_PATH)
    options: dict[str, Any] = {}
    if worker:
        # PostgreSQL owns retries. Surface the original RPC error and flood wait,
        # and disable incoming updates on this upload-only client.
        options = dict(
            receive_updates=False,
            catch_up=False,
            auto_reconnect=False,
            connection_retries=0,
            request_retries=1,
            flood_sleep_threshold=0,
            raise_last_call_error=True,
        )
    return TelegramClient(
        str(settings.TELEGRAM_SESSION_PATH),
        api_id,
        api_hash,
        proxy=proxy_from_settings(settings),
        **options,
    )


class TelegramTransport:
    def __init__(self, client: TelegramClientProtocol) -> None:
        self.client = client

    async def send_text(self, telegram_id: int, text: str, *, buttons: Any = None) -> None:
        # Translation substitutions are escaped before reaching this boundary.
        await self.client.send_message(telegram_id, text, buttons=buttons, parse_mode="html")


class TelegramUploadTransport:
    """One owner for a worker session, uploads, and retiring damaged clients.

    Acquisitions can run concurrently; Telegram sends are serialized so a failed
    client cannot be closed under another upload. Retry timing stays in the DB.
    """

    def __init__(
        self,
        client: TelegramClientProtocol | None = None,
        progress_interval: int = 5,
        *,
        client_factory: Callable[[], TelegramClientProtocol] | None = None,
        bot_token: str | None = None,
        session_path: Path | None = None,
        connect_timeout: float = 30,
        close_timeout: float = 10,
    ) -> None:
        if client is None and client_factory is None:
            raise ValueError("a Telegram client or factory is required")
        self.client = client
        self.progress_interval = progress_interval
        self._factory = client_factory
        self._bot_token = bot_token
        self._session_path = session_path
        self._connect_timeout = connect_timeout
        self._close_timeout = close_timeout
        self._lock = asyncio.Lock()
        self._closing: asyncio.Future[Any] | None = None
        self._closed = False
        self._authorized = False
        self._bootstrap_pending = False
        self._session_lock_fd: int | None = None
        if session_path is not None:
            secure_session_path(session_path)
            descriptor = os.open(
                str(session_path) + ".worker-lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            try:
                state = os.fstat(descriptor)
                if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                    raise ValueError("worker session lock must be a regular file")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            self._session_lock_fd = descriptor

    @classmethod
    def for_worker(cls, settings: Settings) -> "TelegramUploadTransport":
        _, _, token = settings.require_telegram()
        worker_settings = settings.model_copy(
            update={"TELEGRAM_SESSION_PATH": settings.TELEGRAM_WORKER_SESSION_PATH}
        )
        return cls(
            progress_interval=settings.UPLOAD_PROGRESS_INTERVAL_SECONDS,
            client_factory=lambda: build_client(worker_settings, worker=True),
            bot_token=token,
            session_path=settings.TELEGRAM_WORKER_SESSION_PATH,
        )

    @staticmethod
    def _background_error(client: TelegramClientProtocol) -> Exception | None:
        # Telethon 1.x stores fatal update-loop exceptions without propagating
        # them to send_file. Keep this version-specific inspection at one boundary.
        error = getattr(client, "_updates_error", None)
        return error if isinstance(error, Exception) else None

    def is_connected(self) -> bool:
        return bool(
            self.client is not None
            and self._authorized
            and self._closing is None
            and self.client.is_connected()
            and self._background_error(self.client) is None
        )

    async def _retire(self) -> None:
        client = self.client
        if client is None:
            return
        if self._closing is None:
            self._closing = asyncio.ensure_future(client.disconnect())
        try:
            # Telethon shields its disconnect coroutine. Never construct a second
            # client until that coroutine has finished using the SQLite session.
            await asyncio.wait_for(asyncio.shield(self._closing), self._close_timeout)
        except Exception as exc:
            log.warning("telegram_client_close_deferred", exception_class=type(exc).__name__)
            raise TelegramTransportError("Telegram client shutdown is incomplete") from None
        self._closing = None
        self.client = None
        self._authorized = False
        log.info("telegram_client_retired")

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            try:
                await self._retire()
            finally:
                # On a failed shutdown keep the file lock until process exit;
                # another process must not race an unfinished session writer.
                if self.client is None and self._session_lock_fd is not None:
                    os.close(self._session_lock_fd)
                    self._session_lock_fd = None

    async def prepare(self) -> None:
        """Try startup readiness once; delivery retries remain database-owned."""
        async with self._lock:
            try:
                await self._ensure_ready()
            except BaseException:
                with suppress(TelegramTransportError):
                    await self._retire()
                raise

    def _may_bootstrap(self, client: TelegramClientProtocol) -> bool:
        empty_key = not getattr(getattr(client, "session", None), "auth_key", True)
        if self._session_path is None:
            self._bootstrap_pending = self._bootstrap_pending or empty_key
            return self._bootstrap_pending
        marker = Path(str(self._session_path) + ".worker-bootstrap")
        if empty_key:
            # Key exchange can succeed before first bot login is interrupted.
            # Remember this across process restart without resetting the session.
            descriptor = os.open(
                marker, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
            )
            try:
                state = os.fstat(descriptor)
                if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                    raise ValueError("worker bootstrap marker must be a regular file")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        try:
            state = marker.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
            raise ValueError("worker bootstrap marker must be a regular file")
        return True

    async def _ensure_ready(self) -> TelegramClientProtocol:
        if self._closed:
            raise TelegramAuthorizationError("Telegram worker transport is closed")
        if self.client is not None and (
            self._closing is not None
            or (
                self._factory is not None
                and (
                    not self.client.is_connected()
                    or self._background_error(self.client) is not None
                )
            )
        ):
            await self._retire()
        if self.client is None:
            if self._factory is None:
                raise TelegramTransportError("Telegram client factory is unavailable")
            self.client = self._factory()
            log.info("telegram_client_created")
        client = self.client
        async with asyncio.timeout(self._connect_timeout):
            if not client.is_connected():
                # Only bootstrap an empty session. A revoked persisted session is
                # permanent; silently logging it in again would mask revocation.
                empty_session = self._may_bootstrap(client)
                log.info("telegram_client_connect_started")
                await client.connect()
                log.info("telegram_client_connect_completed", connected=client.is_connected())
                if not client.is_connected():
                    raise TelegramTransportError("Telegram client did not reconnect")
            else:
                empty_session = False
            authorized = await client.is_user_authorized()
            if not authorized and empty_session and self._bot_token is not None:
                await client.sign_in(bot_token=self._bot_token)
                authorized = await client.is_user_authorized()
            log.info("telegram_session_verified", authorized=authorized)
            if not authorized:
                raise TelegramAuthorizationError("Telegram worker session is not authorized")
            self._authorized = True
            self._bootstrap_pending = False
        if self._session_path is not None:
            secure_session_path(self._session_path)
            await asyncio.to_thread(
                Path(str(self._session_path) + ".worker-bootstrap").unlink, missing_ok=True
            )
        return client

    async def _watch_client(self, client: TelegramClientProtocol) -> None:
        while True:
            error = self._background_error(client)
            if error is not None:
                kind, _ = classify_delivery_error(error)
                if kind is DeliveryErrorKind.PERMANENT and not isinstance(error, RuntimeError):
                    # Auth/RPC failures retain their permanent classification.
                    raise error
                raise TelegramTransportError("Telegram background loop failed") from None
            if not client.is_connected():
                raise TelegramTransportError("Telegram connection dropped during upload")
            for name in ("_updates_handle", "_keepalive_handle"):
                handle = getattr(client, name, None)
                if handle is not None and handle.done():
                    raise TelegramTransportError("Telegram background task stopped")
            await asyncio.sleep(0.25)

    async def send_file(self, entity: int, file: object, **kwargs: Any) -> Any:
        async with self._lock:
            send: asyncio.Task[Any] | None = None
            watch: asyncio.Task[None] | None = None
            try:
                client = await self._ensure_ready()
                original_progress = kwargs.get("progress_callback")
                last_report = 0.0

                def progress(sent: int, total: int) -> None:
                    nonlocal last_report
                    now = time.monotonic()
                    if now - last_report >= self.progress_interval or sent >= total:
                        last_report = now
                        log.info("telegram_upload_progress", bytes_sent=sent, bytes_total=total)
                    if original_progress is not None:
                        original_progress(sent, total)

                kwargs["progress_callback"] = progress
                log.info("telegram_send_file_started", file_kind=type(file).__name__)
                send = asyncio.create_task(client.send_file(entity, file, **kwargs))
                watch = asyncio.create_task(self._watch_client(client))
                done, _ = await asyncio.wait((send, watch), return_when=asyncio.FIRST_COMPLETED)
                # Preserve an acknowledged success even if the connection then drops.
                if send in done:
                    message = send.result()
                else:
                    watch.result()
                    raise TelegramTransportError("Telegram client monitor stopped")
                log.info("telegram_send_file_succeeded", message_id=getattr(message, "id", None))
                return message
            except BaseException as exc:
                # Cancel the send before retiring the client, including on the
                # durable delivery deadline or worker shutdown cancellation.
                if send is not None and not send.done():
                    send.cancel()
                    await asyncio.gather(send, return_exceptions=True)
                if isinstance(exc, Exception):
                    log.warning("telegram_send_file_failed", exception_class=type(exc).__name__)
                if self._factory is not None and (
                    not isinstance(exc, Exception)
                    or classify_delivery_error(exc)[0] is DeliveryErrorKind.TRANSIENT
                    or not self.is_connected()
                ):
                    with suppress(TelegramTransportError):
                        await self._retire()
                raise
            finally:
                if watch is not None:
                    watch.cancel()
                    await asyncio.gather(watch, return_exceptions=True)
