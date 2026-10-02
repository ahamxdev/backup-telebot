"""Real worker/DB/artifacts and loopback SOCKS5; no public Telegram traffic."""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr
from python_socks import ProxyType
from python_socks.async_.asyncio import Proxy
from sqlalchemy import select

from bootstrap import worker as bootstrap
from database.models import BackupDelivery, BackupPath, BackupProfile, FilesSource, User
from database.session import Database
from domain.enums import (
    CompressionFormat,
    DeliveryStatus,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStatus,
    SourceLocation,
    SourceType,
)
from infrastructure.telegram.transport import TelegramUploadTransport
from pipeline import jobs as jobs_module
from pipeline import worker as worker_module
from pipeline.delivery import DeliveryService
from pipeline.jobs import RunService


class Clock:
    def __init__(self) -> None:
        self.value = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.value


class LoopbackProxy:
    """Minimal SOCKS5 server that acts as a deterministic upload endpoint."""

    def __init__(self) -> None:
        self.server: asyncio.Server | None = None
        self.port = 0
        self.fail_part: str | None = ".part002"
        self.received: list[str] = []
        self.interrupted = asyncio.Event()
        self.handlers: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.handlers.add(task)
        try:
            header = await reader.readexactly(2)
            await reader.readexactly(header[1])
            writer.write(b"\x05\x00")
            await writer.drain()
            request = await reader.readexactly(4)
            if request[3] == 1:
                await reader.readexactly(4)
            elif request[3] == 3:
                length = (await reader.readexactly(1))[0]
                await reader.readexactly(length)
            else:
                await reader.readexactly(16)
            await reader.readexactly(2)
            writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x01\xbb")
            await writer.drain()
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                name, size = json.loads(raw)
                half = size // 2
                await reader.readexactly(half)
                if self.fail_part and name.endswith(self.fail_part):
                    writer.transport.abort()
                    self.interrupted.set()
                    return
                writer.write(b"continue\n")
                await writer.drain()
                await reader.readexactly(size - half)
                self.received.append(name)
                writer.write(b"delivered\n")
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass  # expected when the client is retired or the proxy is stopped
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self.handlers.discard(task)


class SocksUploadClient:
    """Fake Telegram RPCs over a real python-socks TCP transport."""

    def __init__(self, proxy: LoopbackProxy) -> None:
        self.proxy = proxy
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.closed = False

    async def connect(self) -> None:
        proxy = Proxy(ProxyType.SOCKS5, "127.0.0.1", self.proxy.port)
        sock = await proxy.connect(dest_host="192.0.2.1", dest_port=443, timeout=0.5)
        self.reader, self.writer = await asyncio.open_connection(sock=sock)

    def is_connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    async def is_user_authorized(self) -> bool:
        return True

    async def disconnect(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        self.closed = True

    async def send_file(self, entity: int, file: str, **kwargs: Any) -> object:
        del entity
        assert self.reader is not None and self.writer is not None
        data = await asyncio.to_thread(Path(file).read_bytes)
        self.writer.write(json.dumps([Path(file).name, len(data)]).encode() + b"\n")
        half = len(data) // 2
        self.writer.write(data[:half])
        await self.writer.drain()
        kwargs["progress_callback"](half, len(data))
        if await self.reader.readline() != b"continue\n":
            raise ConnectionResetError("proxy disconnected during upload")
        self.writer.write(data[half:])
        await self.writer.drain()
        if await self.reader.readline() != b"delivered\n":
            raise ConnectionResetError("connection dropped during delivery")
        return SimpleNamespace(id=42)

    async def send_message(self, entity: int, message: str, **kwargs: Any) -> None:
        raise AssertionError("worker must only upload")


async def wait_for_delivery(database: Database, predicate) -> BackupDelivery:
    async with asyncio.timeout(5):
        while True:
            async with database.session_factory() as session:
                delivery = await session.scalar(select(BackupDelivery))
                if delivery is not None and predicate(delivery):
                    return delivery
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("outage", ["before_upload", "during_upload"])
async def test_worker_recovers_socks_outage_after_restart_without_reacquisition(
    database, session, settings, tmp_path, monkeypatch, outage
) -> None:
    clock = Clock()
    monkeypatch.setattr(jobs_module, "utc_now", clock)

    class TimedDeliveryService(DeliveryService):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs, now=clock, jitter=lambda _a, _b: 0)

    monkeypatch.setattr(worker_module, "DeliveryService", TimedDeliveryService)

    # This fixture uses metadata.create_all; startup schema tests cover Alembic separately.
    async def schema_checked(_session) -> None:
        pass

    monkeypatch.setattr(bootstrap, "validate_runtime_schema", schema_checked)
    source = tmp_path / "source.txt"
    source.write_text("original source content")
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "ALLOWED_SOURCE_ROOTS": str(tmp_path),
            "MIN_FREE_DISK_BYTES": 0,
            "TELEGRAM_MAX_FILE_BYTES": 4096,
            "WORKER_POLL_SECONDS": 0.01,
            "TELEGRAM_RETRY_BASE_SECONDS": 30,
        }
    )
    owner = User(telegram_user_id=10, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name="Recovery",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        created_by=owner.id,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        files_source=FilesSource(
            location=SourceLocation.LOCAL, paths=[BackupPath(path=str(source))]
        ),
    )
    session.add(profile)
    await session.commit()
    run = (await RunService(session).enqueue_manual(owner, profile.id)).run
    acquisition_calls = 0
    acquire = worker_module.acquire

    async def counted_acquisition(*args, **kwargs):
        nonlocal acquisition_calls
        acquisition_calls += 1
        return await acquire(*args, **kwargs)

    monkeypatch.setattr(worker_module, "acquire", counted_acquisition)
    proxy = LoopbackProxy()
    await proxy.start()
    if outage == "before_upload":
        await proxy.stop()
    clients: list[SocksUploadClient] = []
    transports: list[TelegramUploadTransport] = []

    def factory() -> SocksUploadClient:
        client = SocksUploadClient(proxy)
        clients.append(client)
        return client

    def transport_factory(_settings) -> TelegramUploadTransport:
        transport = TelegramUploadTransport(client_factory=factory)
        transports.append(transport)
        return transport

    monkeypatch.setattr(bootstrap.TelegramUploadTransport, "for_worker", transport_factory)
    worker = asyncio.create_task(bootstrap.run_worker(configured))
    restarted: Database | None = None
    try:
        delivery = await wait_for_delivery(
            database, lambda d: d.part_attempts == 1 and d.last_error
        )
        if outage == "during_upload":
            assert proxy.interrupted.is_set()
            await proxy.stop()
        assert not worker.done()
        await session.refresh(run)
        spool = Path(run.pending_directory)
        assert await asyncio.to_thread(spool.is_dir) and run.status is RunStatus.DELIVERY_PENDING
        assert delivery.status is DeliveryStatus.PENDING
        assert delivery.next_part == (2 if outage == "during_upload" else 1)
        assert acquisition_calls == 1
        source.unlink()  # recovery cannot silently reacquire the source
        attempts = delivery.attempts
        await asyncio.sleep(0.05)
        assert (await wait_for_delivery(database, lambda _d: True)).attempts == attempts
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert all(client.closed for client in clients)
        # A fresh worker, engine, session, and transport must use persisted retry times.
        restarted = Database(configured.DATABASE_URL)
        worker = asyncio.create_task(bootstrap.run_worker(configured))
        await asyncio.sleep(0.05)
        assert not worker.done()
        assert (await wait_for_delivery(restarted, lambda _d: True)).attempts == attempts
        clock.value += timedelta(seconds=31)
        failed_again = await wait_for_delivery(restarted, lambda d: d.attempts == attempts + 1)
        assert failed_again.next_part == delivery.next_part
        assert await asyncio.to_thread(spool.is_dir)
        proxy.fail_part = None
        await proxy.start()
        clock.value += timedelta(seconds=61)
        await wait_for_delivery(restarted, lambda d: d.status is DeliveryStatus.SUCCEEDED)
        async with asyncio.timeout(5):
            while await asyncio.to_thread(spool.exists):  # noqa: ASYNC110 - external FS state
                await asyncio.sleep(0.01)
        async with asyncio.timeout(5):
            while True:
                await session.refresh(run)
                if run.pending_directory is None:
                    break
                await asyncio.sleep(0.01)
        assert run.status is RunStatus.SUCCEEDED and run.pending_directory is None
        assert acquisition_calls == 1
        assert len(proxy.received) == len(set(proxy.received))
        assert len(proxy.received) > 1
        assert not worker.done()
        assert len(transports) == 2
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await proxy.stop()
        if restarted is not None:
            await restarted.dispose()
