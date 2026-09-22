"""Acquire one native GitLab backup from an operator-approved bind mount."""

import asyncio
import fcntl
import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from application.profile_config import GitLabConfig
from config.settings import Settings
from database.models import GitLabSource
from pipeline.acquisition.common import SourceArtifact
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError

_SUFFIX = "_gitlab_backup.tar"


@dataclass(frozen=True)
class _FileState:
    name: str
    inode: int
    size: int
    mtime_ns: int


def _approved_root(source: GitLabSource, settings: Settings) -> Path:
    GitLabConfig(
        source.container_name,
        source.host_backup_path,
        source.worker_backup_path,
    ).validate(settings.GITLAB_BACKUP_HOST_ROOT, str(settings.GITLAB_BACKUP_WORKER_ROOT))
    root = Path(source.worker_backup_path)
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PipelineError("GITLAB_ROOT_INVALID", "GitLab backup root is unavailable") from exc
    if root.is_symlink() or resolved != root or not root.is_dir():
        raise PipelineError("GITLAB_ROOT_INVALID", "GitLab backup root must not use symlinks")
    return root


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise PipelineError("GITLAB_PATH_ESCAPE", "unsafe entry in GitLab root") from exc


def _validate_tree_fd(directory_fd: int) -> None:
    for entry in os.scandir(directory_fd):
        if entry.is_symlink():
            raise PipelineError("GITLAB_PATH_ESCAPE", "symlinks are forbidden in GitLab root")
        if entry.is_dir(follow_symlinks=False):
            child_fd = _open_child_directory(directory_fd, entry.name)
            try:
                _validate_tree_fd(child_fd)
            finally:
                os.close(child_fd)


def _delete_tree_fd(directory_fd: int) -> None:
    for entry in os.scandir(directory_fd):
        if entry.is_symlink():
            raise PipelineError("GITLAB_PATH_ESCAPE", "symlinks are forbidden in GitLab root")
        if entry.is_dir(follow_symlinks=False):
            child_fd = _open_child_directory(directory_fd, entry.name)
            try:
                _delete_tree_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(entry.name, dir_fd=directory_fd)
        else:
            os.unlink(entry.name, dir_fd=directory_fd)


def cleanup_old_backups(root: Path) -> None:
    """Delete root contents through a no-follow directory descriptor, never the root."""
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PipelineError("GITLAB_ROOT_INVALID", "GitLab backup root is unsafe") from exc
    try:
        _validate_tree_fd(root_fd)
        _delete_tree_fd(root_fd)
    finally:
        os.close(root_fd)


def _snapshot(root: Path) -> dict[str, _FileState]:
    result: dict[str, _FileState] = {}
    for path in root.iterdir():
        if path.is_symlink():
            raise PipelineError("GITLAB_PATH_ESCAPE", "GitLab artifact cannot be a symlink")
        info = path.stat(follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and path.name.endswith(_SUFFIX):
            result[path.name] = _FileState(path.name, info.st_ino, info.st_size, info.st_mtime_ns)
    return result


def detect_new_backup(
    before: dict[str, _FileState], after: dict[str, _FileState], started_ns: int
) -> _FileState:
    candidates = [
        item
        for name, item in after.items()
        if (name not in before or before[name].inode != item.inode)
        and item.mtime_ns >= started_ns - 1_000_000_000
        and item.size > 0
    ]
    if len(candidates) != 1:
        raise PipelineError(
            "GITLAB_ARTIFACT_AMBIGUOUS",
            f"expected exactly one new GitLab backup, found {len(candidates)}",
        )
    return candidates[0]


async def _stable_file(path: Path, settings: Settings, deadline: RunDeadline | None) -> _FileState:
    previous: _FileState | None = None
    stable = 0
    while stable < settings.GITLAB_STABILITY_CHECKS:
        if deadline is not None:
            deadline.check()
        if path.is_symlink():
            raise PipelineError("GITLAB_PATH_ESCAPE", "GitLab artifact cannot be a symlink")
        info = path.stat(follow_symlinks=False)
        current = _FileState(path.name, info.st_ino, info.st_size, info.st_mtime_ns)
        if not stat.S_ISREG(info.st_mode) or current.size <= 0:
            raise PipelineError("GITLAB_ARTIFACT_INVALID", "GitLab artifact is not a regular file")
        stable = stable + 1 if current == previous else 1
        previous = current
        if stable < settings.GITLAB_STABILITY_CHECKS:
            await asyncio.sleep(settings.GITLAB_STABILITY_INTERVAL_SECONDS)
    return current


async def _invoke_helper(source: GitLabSource, settings: Settings, deadline_seconds: float) -> None:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(settings.GITLAB_HELPER_SOCKET), timeout=deadline_seconds
        )
        request = {"operation": "gitlab_backup_create", "container": source.container_name}
        writer.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=deadline_seconds)
        writer.close()
        await writer.wait_closed()
        response = json.loads(raw)
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError("GITLAB_HELPER_FAILED", "GitLab helper is unavailable") from exc
    if not response.get("ok") or response.get("exit_code") != 0:
        raise PipelineError("GITLAB_BACKUP_FAILED", "native GitLab backup command failed")


def _copy_and_verify(source: Path, destination: Path, expected: _FileState) -> tuple[int, str]:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(source_fd)
        if before.st_ino != expected.inode or before.st_size != expected.size:
            raise PipelineError(
                "GITLAB_SOURCE_CHANGED", "GitLab artifact changed before acquisition"
            )
        digest = hashlib.sha256()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.dup(source_fd), "rb") as source_stream, destination.open("xb") as target:
            _copy_hash(source_stream, target, digest)
            target.flush()
            os.fsync(target.fileno())
        after = os.fstat(source_fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            destination.unlink(missing_ok=True)
            raise PipelineError(
                "GITLAB_SOURCE_CHANGED", "GitLab artifact changed during acquisition"
            )
        copied = destination.stat()
        copied_hash = hashlib.sha256()
        with destination.open("rb") as copied_stream:
            while chunk := copied_stream.read(1024 * 1024):
                copied_hash.update(chunk)
        copied_digest = copied_hash.hexdigest()
        if copied.st_size != before.st_size or copied_digest != digest.hexdigest():
            destination.unlink(missing_ok=True)
            raise PipelineError(
                "GITLAB_ACQUISITION_FAILED", "GitLab artifact copy failed validation"
            )
        return copied.st_size, copied_digest
    finally:
        os.close(source_fd)


def _copy_hash(source: BinaryIO, target: BinaryIO, digest: Any) -> None:
    while chunk := source.read(1024 * 1024):
        target.write(chunk)
        digest.update(chunk)


def _unlink_verified(root: Path, expected: _FileState) -> None:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.stat(expected.name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_ino != expected.inode
            or current.st_size != expected.size
            or current.st_mtime_ns != expected.mtime_ns
        ):
            raise PipelineError("GITLAB_SOURCE_CHANGED", "GitLab artifact changed before cleanup")
        os.unlink(expected.name, dir_fd=root_fd)
    finally:
        os.close(root_fd)


@contextmanager
def _exclusive_lock(settings: Settings) -> Iterator[None]:
    lock_path = settings.GITLAB_HELPER_SOCKET.parent / "backup.lock"
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineError("GITLAB_BUSY", "another GitLab backup is active") from exc
        yield


async def acquire_gitlab(
    source: GitLabSource,
    destination: Path,
    settings: Settings,
    deadline: RunDeadline | None,
) -> SourceArtifact:
    root = _approved_root(source, settings)
    with _exclusive_lock(settings):
        cleanup_old_backups(root)
        before = _snapshot(root)
        started_ns = time.time_ns()
        deadline_seconds = (
            deadline.remaining() if deadline is not None else settings.MAX_BACKUP_RUNTIME_SECONDS
        )
        await _invoke_helper(source, settings, deadline_seconds)
        detected = detect_new_backup(before, _snapshot(root), started_ns)
        native = root / detected.name
        stable = await _stable_file(native, settings, deadline)
        target = destination.with_suffix(".gitlab.tar")
        size, checksum = await asyncio.to_thread(_copy_and_verify, native, target, stable)
        _unlink_verified(root, stable)
    return SourceArtifact(
        target,
        ".gitlab.tar",
        {
            "kind": "gitlab",
            "native_filename": detected.name,
            "native_size": size,
            "native_sha256": checksum,
            "source_cleanup": "succeeded",
            "verified": True,
        },
    )
