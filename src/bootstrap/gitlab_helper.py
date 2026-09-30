"""Narrow privileged broker for acquiring a local GitLab native backup."""

import asyncio
import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import docker

from application.profile_config import validate_absolute_path, validate_docker_container_name

_MAX_REQUEST_BYTES = 4096
_MAX_OUTPUT_BYTES = 16_384
_SUFFIX = "_gitlab_backup.tar"
_EXECUTION_LOCK = asyncio.Lock()


class AcquisitionError(RuntimeError):
    """A safely reportable local GitLab acquisition failure."""


@dataclass(frozen=True)
class FileState:
    name: str
    inode: int
    size: int
    mtime_ns: int


def _configured_directory(variable: str, default: str) -> Path:
    configured = validate_absolute_path(os.environ.get(variable, default))
    if configured == "/":
        raise ValueError(f"{variable} cannot be filesystem root")
    path = Path(configured)
    resolved = path.resolve(strict=True)
    if resolved != path or not path.is_dir():
        raise ValueError(f"{variable} must be a real directory without symlinks")
    return path


def _run_gitlab_backup(container_name: str) -> tuple[int, str]:
    """Run the sole Docker operation exposed by this helper, without a shell."""
    container_name = validate_docker_container_name(container_name)
    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
        result = container.exec_run(["gitlab-backup", "create"], tty=True, demux=False)
        output = result.output if isinstance(result.output, bytes) else b""
        return int(result.exit_code), output[-_MAX_OUTPUT_BYTES:].decode(errors="replace")
    finally:
        client.close()


def _snapshot(root: Path) -> dict[str, FileState]:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        result: dict[str, FileState] = {}
        with os.scandir(root_fd) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise AcquisitionError("symlinks are forbidden in the GitLab backup root")
                info = entry.stat(follow_symlinks=False)
                if entry.name.endswith(_SUFFIX) and stat.S_ISREG(info.st_mode):
                    result[entry.name] = FileState(
                        entry.name, info.st_ino, info.st_size, info.st_mtime_ns
                    )
        return result
    finally:
        os.close(root_fd)


def _detect_new_backup(
    before: dict[str, FileState], after: dict[str, FileState], started_ns: int
) -> FileState:
    candidates = [
        item
        for name, item in after.items()
        if (name not in before or before[name].inode != item.inode)
        and item.mtime_ns >= started_ns - 1_000_000_000
        and item.size > 0
    ]
    if len(candidates) != 1:
        raise AcquisitionError(f"expected exactly one new GitLab backup, found {len(candidates)}")
    return candidates[0]


def _stable_file(root: Path, expected: FileState, checks: int, interval: float) -> FileState:
    previous: FileState | None = None
    stable = 0
    while stable < checks:
        current = _snapshot(root).get(expected.name)
        if current is None or current.size <= 0:
            raise AcquisitionError("GitLab artifact disappeared or is empty")
        stable = stable + 1 if current == previous else 1
        previous = current
        if stable < checks:
            time.sleep(interval)
    if previous is None:
        raise AcquisitionError("GitLab artifact stability check failed")
    return previous


def _copy_hash(source: BinaryIO, target: BinaryIO, digest: Any) -> None:
    while chunk := source.read(1024 * 1024):
        target.write(chunk)
        digest.update(chunk)


def _stage_verified(root: Path, staging: Path, expected: FileState) -> tuple[str, int, str]:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = f"{uuid.uuid4().hex}.gitlab.tar"
    try:
        source_fd = os.open(expected.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode) or (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (expected.inode, expected.size, expected.mtime_ns):
                raise AcquisitionError("GitLab artifact changed before acquisition")
            target_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=staging_fd,
            )
            digest = hashlib.sha256()
            try:
                with (
                    os.fdopen(os.dup(source_fd), "rb") as source_stream,
                    os.fdopen(os.dup(target_fd), "wb") as target_stream,
                ):
                    _copy_hash(source_stream, target_stream, digest)
                    target_stream.flush()
                    os.fsync(target_stream.fileno())
                os.fchown(target_fd, 10001, 10001)
            except Exception:
                os.unlink(name, dir_fd=staging_fd)
                raise
            finally:
                os.close(target_fd)
            after = os.fstat(source_fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                os.unlink(name, dir_fd=staging_fd)
                raise AcquisitionError("GitLab artifact changed during acquisition")
            staged_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=staging_fd)
            try:
                staged = os.fstat(staged_fd)
                staged_digest = hashlib.sha256()
                while chunk := os.read(staged_fd, 1024 * 1024):
                    staged_digest.update(chunk)
            finally:
                os.close(staged_fd)
            checksum = digest.hexdigest()
            if staged.st_size != before.st_size or staged_digest.hexdigest() != checksum:
                os.unlink(name, dir_fd=staging_fd)
                raise AcquisitionError("staged GitLab artifact failed verification")
            current = os.stat(expected.name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ) != (expected.inode, expected.size, expected.mtime_ns):
                os.unlink(name, dir_fd=staging_fd)
                raise AcquisitionError("GitLab artifact changed before cleanup")
            # Native cleanup is last: the staged copy was re-read and verified above.
            os.unlink(expected.name, dir_fd=root_fd)
            return name, staged.st_size, checksum
        finally:
            os.close(source_fd)
    finally:
        os.close(staging_fd)
        os.close(root_fd)


def _acquire(container_name: str) -> dict[str, Any]:
    root = _configured_directory("GITLAB_BACKUP_ROOT", "/gitlab-backups")
    staging = _configured_directory("GITLAB_STAGING_ROOT", "/data/gitlab-staging")
    before = _snapshot(root)
    started_ns = time.time_ns()
    exit_code, output = _run_gitlab_backup(container_name)
    if exit_code != 0:
        raise AcquisitionError(f"native GitLab backup failed: {output[-512:]}")
    detected = _detect_new_backup(before, _snapshot(root), started_ns)
    checks = int(os.environ.get("GITLAB_STABILITY_CHECKS", "2"))
    interval = float(os.environ.get("GITLAB_STABILITY_INTERVAL_SECONDS", "1"))
    if not 2 <= checks <= 20 or not 0.05 <= interval <= 60:
        raise ValueError("invalid GitLab stability configuration")
    stable = _stable_file(root, detected, checks, interval)
    staging_name, size, checksum = _stage_verified(root, staging, stable)
    return {
        "ok": True,
        "staging_name": staging_name,
        "native_name": stable.name,
        "size": size,
        "sha256": checksum,
    }


def _validated_request(raw: bytes) -> str:
    if not raw or len(raw) > _MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid request")
    request = json.loads(raw)
    if not isinstance(request, dict) or set(request) != {"operation", "container"}:
        raise ValueError("invalid request fields")
    if request["operation"] != "gitlab_backup_acquire":
        raise ValueError("operation is not allowed")
    return validate_docker_container_name(str(request["container"]))


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    response: dict[str, Any]
    try:
        raw = await reader.readline()
        container = _validated_request(raw)
        async with _EXECUTION_LOCK:
            response = await asyncio.to_thread(_acquire, container)
    except Exception as exc:
        response = {"ok": False, "error": type(exc).__name__}
    writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def _prepare_shared_paths(socket_path: Path) -> None:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(socket_path.parent, 10001, 10001)
    os.chmod(socket_path.parent, 0o700)
    staging = Path(os.environ.get("GITLAB_STAGING_ROOT", "/data/gitlab-staging"))
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(staging, 10001, 10001)
    os.chmod(staging, 0o700)
    if socket_path.exists():
        socket_path.unlink()


async def serve(socket_path: Path) -> None:
    _prepare_shared_paths(socket_path)
    server = await asyncio.start_unix_server(_handle, path=socket_path)
    os.chown(socket_path, 10001, 10001)
    os.chmod(socket_path, 0o600)
    async with server:
        await server.serve_forever()


def entrypoint() -> None:
    socket_path = Path(os.environ.get("GITLAB_HELPER_SOCKET", "/run/gitlab-helper/helper.sock"))
    if not socket_path.is_absolute():
        raise ValueError("GITLAB_HELPER_SOCKET must be absolute")
    asyncio.run(serve(socket_path))
