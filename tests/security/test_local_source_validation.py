import asyncio
import json
import os
import stat
from pathlib import Path

import pytest
import pytest_asyncio

from application.profile_config import LocalSourcePathError
from bootstrap import local_source_validation as server_module
from bootstrap.local_source_validation import LocalSourceValidationServer
from config.settings import Settings
from database.models import BackupPath, BackupProfile, FilesSource
from domain.enums import SourceLocation
from infrastructure import local_source_validation as client_module
from infrastructure.local_source_validation import (
    MAX_MESSAGE_BYTES,
    LocalValidationUnavailable,
    normalize_worker_path,
    validate_local_source,
)
from pipeline.acquisition.local_files import _package_local
from pipeline.errors import PipelineError


@pytest_asyncio.fixture
async def validation_service(settings: Settings, tmp_path: Path):
    root = tmp_path / "approved"
    root.mkdir()
    (root / "file").write_text("PRIVATE-FILE-CONTENT")
    (root / "directory").mkdir()
    configured = settings.model_copy(
        update={
            "ALLOWED_SOURCE_ROOTS": "/private-host",
            "LOCAL_SOURCE_MOUNTS": f"/private-host:{root}",
            "MIN_FREE_DISK_BYTES": 0,
        }
    )
    server = LocalSourceValidationServer(configured)
    await server.start()
    try:
        yield configured, root, server
    finally:
        await server.close()


async def request(settings: Settings, raw: bytes) -> dict:
    reader, writer = await asyncio.open_unix_connection(
        str(settings.LOCAL_SOURCE_VALIDATION_SOCKET)
    )
    try:
        writer.write(raw)
        await writer.drain()
        async with asyncio.timeout(2):
            return json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("name,kind", [("file", "file"), ("directory", "directory")])
async def test_valid_sources_have_only_safe_metadata(validation_service, name, kind) -> None:
    settings, root, _ = validation_service
    path = str(root / name)
    response = await request(
        settings, json.dumps({"operation": "validate_local_source", "path": path}).encode() + b"\n"
    )
    assert response == {"valid": True, "path": path, "type": kind}
    assert "PRIVATE-FILE-CONTENT" not in json.dumps(response)
    assert "/private-host" not in json.dumps(response)
    assert await validate_local_source(path, settings) == path
    assert stat.S_IMODE(settings.LOCAL_SOURCE_VALIDATION_SOCKET.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.LOCAL_SOURCE_VALIDATION_SOCKET.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("suffix", ["missing", "../outside", "escape", "escape/child", "fifo"])
async def test_invalid_filesystem_sources_rejected(validation_service, suffix) -> None:
    settings, root, _ = validation_service
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "child").write_text("SECRET")
    (root / "escape").symlink_to(outside)
    os.mkfifo(root / "fifo")
    with pytest.raises(LocalSourcePathError):
        await validate_local_source(f"{root}/{suffix}", settings)
    # A compromised client that skips bot-side checks is also rejected by the worker.
    response = await request(
        settings,
        json.dumps({"operation": "validate_local_source", "path": f"{root}/{suffix}"}).encode()
        + b"\n",
    )
    assert response["valid"] is False
    assert set(response) == {"valid", "reason"}


@pytest.mark.parametrize("path", ["/private-host/file", "/outside", "relative", "/a\x00b"])
async def test_lexical_rejections_never_contact_worker(settings: Settings, path: str) -> None:
    with pytest.raises(LocalSourcePathError):
        await validate_local_source(path, settings)


async def test_unsafe_root_symlink_rejected(validation_service) -> None:
    settings, root, _ = validation_service
    real = root.with_name("real")
    root.rename(real)
    root.symlink_to(real)
    with pytest.raises(LocalSourcePathError):
        await validate_local_source(str(root), settings)


async def test_unreadable_source_rejected(validation_service, monkeypatch) -> None:
    settings, root, _ = validation_service
    from pipeline.acquisition import local_files

    original = local_files._open_local_root

    def denied(path):
        if path == root / "file":
            raise PermissionError("raw private host details")
        return original(path)

    monkeypatch.setattr(local_files, "_open_local_root", denied)
    with pytest.raises(LocalSourcePathError):
        await validate_local_source(str(root / "file"), settings)


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json\n",
        b"[]\n",
        b'{"operation":"read_file","path":"/etc/passwd"}\n',
        b'{"operation":"stat","path":"/etc/passwd"}\n',
        b'{"operation":"exec","command":"id"}\n',
        b'{"operation":"validate_local_source","path":42}\n',
        b'{"operation":"validate_local_source","path":"/etc","extra":true}\n',
        b"x" * (MAX_MESSAGE_BYTES + 1) + b"\n",
    ],
)
async def test_malformed_oversized_and_arbitrary_operations_rejected(
    validation_service, raw
) -> None:
    settings, _, _ = validation_service
    assert await request(settings, raw) == {"valid": False, "reason": "invalid_request"}


async def test_unavailable_validation_service_fails_closed(validation_service) -> None:
    settings, root, server = validation_service
    await server.close()
    with pytest.raises(LocalValidationUnavailable):
        await validate_local_source(str(root / "file"), settings)


async def test_stalled_request_times_out(validation_service, monkeypatch) -> None:
    settings, _, _ = validation_service
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 0.05)
    reader, writer = await asyncio.open_unix_connection(
        str(settings.LOCAL_SOURCE_VALIDATION_SOCKET)
    )
    try:
        writer.write(b'{"operation":')
        await writer.drain()
        async with asyncio.timeout(1):
            assert json.loads(await reader.readline()) == {
                "valid": False,
                "reason": "service_unavailable",
            }
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize(
    "reply",
    [
        b'{"valid":true,"path":"/host","type":"file","contents":"SECRET"}\n',
        b'{"valid":false,"reason":[]}\n',
        b"[" * 1500 + b"]" * 1500 + b"\n",
    ],
)
async def test_invalid_reply_fails_closed(settings, tmp_path, monkeypatch, reply) -> None:
    configured = settings.model_copy(update={"ALLOWED_SOURCE_ROOTS": str(tmp_path)})
    monkeypatch.setattr(client_module, "TIMEOUT_SECONDS", 0.05)

    async def bad_reply(reader, writer):
        await reader.readline()
        writer.write(reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    configured.LOCAL_SOURCE_VALIDATION_SOCKET.parent.mkdir()
    server = await asyncio.start_unix_server(
        bad_reply, path=configured.LOCAL_SOURCE_VALIDATION_SOCKET
    )
    try:
        with pytest.raises(LocalValidationUnavailable):
            await validate_local_source(str(tmp_path), configured)
    finally:
        server.close()
        await server.wait_closed()


async def test_concurrency_is_bounded(validation_service) -> None:
    settings, _, server = validation_service
    streams = []
    try:
        for _ in range(server_module.MAX_CONCURRENT_REQUESTS):
            streams.append(
                await asyncio.open_unix_connection(str(settings.LOCAL_SOURCE_VALIDATION_SOCKET))
            )
        reader, writer = await asyncio.open_unix_connection(
            str(settings.LOCAL_SOURCE_VALIDATION_SOCKET)
        )
        async with asyncio.timeout(1):
            assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
        assert len(server.connections) <= server_module.MAX_CONCURRENT_REQUESTS
    finally:
        for _, writer in streams:
            writer.close()
            await writer.wait_closed()


async def test_acquisition_revalidates_after_preflight(validation_service) -> None:
    settings, root, _ = validation_service
    path = root / "file"
    assert await validate_local_source(str(path), settings) == str(path)
    path.unlink()
    outside = root.parent / "secret"
    outside.write_text("SECRET")
    path.symlink_to(outside)
    profile = BackupProfile(
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path=str(path), sqlite_snapshot_candidate=False)],
        )
    )
    with pytest.raises(PipelineError):
        _package_local(profile, root.parent / "backup.tar", settings)


def test_bot_normalization_never_inspects_source_filesystem(settings, monkeypatch) -> None:
    configured = settings.model_copy(
        update={"ALLOWED_SOURCE_ROOTS": "/etc", "LOCAL_SOURCE_MOUNTS": "/etc:/host/etc"}
    )
    original = Path.resolve

    def guarded_resolve(path, *args, **kwargs):
        assert not str(path).startswith(("/host/etc", "/etc"))
        return original(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("bot inspected source filesystem")

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    assert normalize_worker_path("/host/etc/./nginx//", configured) == "/host/etc/nginx"


async def test_client_stalled_response_times_out(validation_service, monkeypatch) -> None:
    settings, root, _ = validation_service
    monkeypatch.setattr(client_module, "TIMEOUT_SECONDS", 0.05)
    ready = asyncio.Event()
    release = asyncio.Event()

    async def stalled(reader, writer):
        try:
            await reader.readline()
            ready.set()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    stalled_path = settings.LOCAL_SOURCE_VALIDATION_SOCKET.with_name("stalled.sock")
    configured = settings.model_copy(update={"LOCAL_SOURCE_VALIDATION_SOCKET": stalled_path})
    server = await asyncio.start_unix_server(stalled, path=stalled_path)
    try:
        with pytest.raises(LocalValidationUnavailable):
            await validate_local_source(str(root), configured)
        assert ready.is_set()
    finally:
        release.set()
        server.close()
        await server.wait_closed()


async def test_timed_out_filesystem_jobs_keep_concurrency_slots(validation_service, monkeypatch):
    import threading

    settings, root, server = validation_service
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    def blocked(*args):
        release.wait(2)
        return {"valid": False, "reason": "unavailable"}

    monkeypatch.setattr(server_module, "_validate", blocked)
    raw = json.dumps({"operation": "validate_local_source", "path": str(root)}).encode() + b"\n"
    try:
        for _ in range(server_module.MAX_CONCURRENT_REQUESTS + 1):
            assert await request(settings, raw) == {
                "valid": False,
                "reason": "service_unavailable",
            }
        assert len(server.jobs) == server_module.MAX_CONCURRENT_REQUESTS
    finally:
        release.set()
        await asyncio.gather(*tuple(server.jobs))
