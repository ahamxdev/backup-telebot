"""Worker-owned Unix socket. One bounded JSON request/response per connection."""

import asyncio
import json
import os
import stat
from contextlib import suppress
from typing import Any

from application.profile_config import LocalSourcePathError
from config.settings import Settings
from infrastructure.local_source_validation import (
    INVALID_REASONS,
    MAX_CONCURRENT_REQUESTS,
    MAX_MESSAGE_BYTES,
    TIMEOUT_SECONDS,
    normalize_worker_path,
)
from pipeline.acquisition.local_files import preflight_local_source
from pipeline.errors import PipelineError


def _request_path(raw: bytes, settings: Settings) -> str:
    if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid request")
    request = json.loads(raw)
    if (
        not isinstance(request, dict)
        or set(request) != {"operation", "path"}
        or request["operation"] != "validate_local_source"
        or not isinstance(request["path"], str)
    ):
        raise ValueError("invalid request")
    return normalize_worker_path(request["path"], settings)


def _validate(path: str, settings: Settings) -> dict[str, Any]:
    try:
        kind = preflight_local_source(path, settings)
        return {"valid": True, "path": path, "type": kind}
    except PipelineError as error:
        reason = error.reason if error.reason in INVALID_REASONS else "source_changed"
        return {"valid": False, "reason": reason}
    except (OSError, ValueError):
        return {"valid": False, "reason": "unavailable"}


class LocalSourceValidationServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.server: asyncio.Server | None = None
        self.connections: set[asyncio.Task[None]] = set()
        self.jobs: set[asyncio.Task[dict[str, Any]]] = set()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Bound handlers as well as filesystem jobs; excess callers fail closed.
        if len(self.connections) >= MAX_CONCURRENT_REQUESTS:
            writer.close()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self.connections.add(task)
        task.add_done_callback(self.connections.discard)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, Any] = {"valid": False, "reason": "service_unavailable"}
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                raw = await reader.readline()
                path = _request_path(raw, self.settings)
                if len(self.jobs) < MAX_CONCURRENT_REQUESTS:
                    task = asyncio.create_task(asyncio.to_thread(_validate, path, self.settings))
                    self.jobs.add(task)
                    task.add_done_callback(self._job_done)
                    # Timeout cannot stop a filesystem syscall. Retain its slot until it ends.
                    response = await asyncio.shield(task)
        except LocalSourcePathError as error:
            response = {"valid": False, "reason": error.reason}
        except (ValueError, UnicodeError):
            response = {"valid": False, "reason": "invalid_request"}
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            writer.close()
            raise
        except Exception:
            response = {"valid": False, "reason": "service_unavailable"}
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
                await writer.drain()
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    await writer.wait_closed()

    def _job_done(self, task: asyncio.Task[dict[str, Any]]) -> None:
        self.jobs.discard(task)
        if not task.cancelled():
            task.exception()

    async def start(self) -> None:
        await asyncio.to_thread(self._prepare_socket)
        self.server = await asyncio.start_unix_server(
            self._accept,
            path=str(self.settings.LOCAL_SOURCE_VALIDATION_SOCKET),
            limit=MAX_MESSAGE_BYTES,
            backlog=MAX_CONCURRENT_REQUESTS,
        )
        await asyncio.to_thread(os.chmod, self.settings.LOCAL_SOURCE_VALIDATION_SOCKET, 0o600)

    def _prepare_socket(self) -> None:
        path = self.settings.LOCAL_SOURCE_VALIDATION_SOCKET
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise ValueError("validation control directory must be a real directory")
        os.chmod(path.parent, 0o700)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode):
            raise ValueError("validation socket path contains an unexpected object")
        path.unlink()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        tasks = tuple(self.connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
