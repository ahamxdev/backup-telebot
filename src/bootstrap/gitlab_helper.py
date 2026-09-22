"""Narrow Docker-socket proxy for GitLab's native backup command."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import docker

from application.profile_config import validate_docker_container_name

_MAX_REQUEST_BYTES = 4096
_MAX_OUTPUT_BYTES = 16_384
_EXECUTION_LOCK = asyncio.Lock()


def _run_gitlab_backup(container_name: str) -> tuple[int, str]:
    """Run the sole command exposed by this helper, without a shell."""
    container_name = validate_docker_container_name(container_name)
    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
        result = container.exec_run(["gitlab-backup", "create"], tty=True, demux=False)
        output = result.output if isinstance(result.output, bytes) else b""
        return int(result.exit_code), output[-_MAX_OUTPUT_BYTES:].decode(errors="replace")
    finally:
        client.close()


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    response: dict[str, Any]
    try:
        raw = await reader.readline()
        if not raw or len(raw) > _MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            raise ValueError("invalid request")
        request = json.loads(raw)
        if set(request) != {"operation", "container"}:
            raise ValueError("invalid request fields")
        if request["operation"] != "gitlab_backup_create":
            raise ValueError("operation is not allowed")
        container = validate_docker_container_name(str(request["container"]))
        async with _EXECUTION_LOCK:
            exit_code, output = await asyncio.to_thread(_run_gitlab_backup, container)
        response = {"ok": exit_code == 0, "exit_code": exit_code, "output": output}
    except Exception as exc:
        response = {"ok": False, "exit_code": None, "output": type(exc).__name__}
    writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def _prepare_socket(socket_path: Path) -> None:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()


async def serve(socket_path: Path) -> None:
    _prepare_socket(socket_path)
    server = await asyncio.start_unix_server(_handle, path=socket_path)
    os.chmod(socket_path, 0o600)
    async with server:
        await server.serve_forever()


def entrypoint() -> None:
    socket_path = Path(os.environ.get("GITLAB_HELPER_SOCKET", "/run/gitlab-helper/helper.sock"))
    if not socket_path.is_absolute():
        raise ValueError("GITLAB_HELPER_SOCKET must be absolute")
    asyncio.run(serve(socket_path))
