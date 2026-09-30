"""Acquire one native GitLab backup from a local bind mount or trusted remote host."""

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import asyncssh

from application.profile_config import (
    GitLabConfig,
    LocalSourcePathError,
    LocalSourcePathMapper,
    validate_docker_container_name,
)
from config.settings import Settings
from database.models import GitLabSource
from domain.enums import SourceLocation
from infrastructure.ssh import HostKeyChangedError, SSHConnectionTimeout, SSHGateway
from pipeline.acquisition.common import SourceArtifact, SourceBudget
from pipeline.acquisition.remote_files import (
    _rsync_download,
    _ssh_credentials,
    _validate_remote_path,
)
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from security import SecretCipher

_SUFFIX = "_gitlab_backup.tar"


@dataclass(frozen=True)
class _FileState:
    name: str
    inode: int
    size: int
    mtime_ns: int


def _approved_root(source: GitLabSource, settings: Settings) -> Path:
    GitLabConfig(source.location, source.container_name, source.backup_path).validate()
    try:
        validated = LocalSourcePathMapper(
            settings.allowed_source_roots(),
            settings.denied_source_roots(),
            settings.local_source_mounts(),
        ).validate_writable_container_directory(source.backup_path)
    except LocalSourcePathError as exc:
        raise PipelineError(
            "GITLAB_ROOT_INVALID", "GitLab backup root is unavailable or unsafe", reason=exc.reason
        ) from exc
    return Path(validated)


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


async def _acquire_local_gitlab(
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
        size,
    )


_REMOTE_FILE_SCRIPT = """
import hashlib,json,os,stat,sys
operation,root=sys.argv[1:3]
suffix='_gitlab_backup.tar'
rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    if operation == 'snapshot':
        result=[]
        with os.scandir(rootfd) as entries:
            for entry in entries:
                info=entry.stat(follow_symlinks=False)
                if entry.name.endswith(suffix) and stat.S_ISREG(info.st_mode):
                    result.append([entry.name,info.st_ino,info.st_size,info.st_mtime_ns])
        print(json.dumps(result,separators=(',',':')))
    else:
        name,inode,size,mtime=sys.argv[3:7]
        if '/' in name or name in {'.','..'} or not name.endswith(suffix):
            raise ValueError('unsafe GitLab artifact name')
        expected=(int(inode),int(size),int(mtime))
        descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=rootfd)
        try:
            info=os.fstat(descriptor)
            actual=(info.st_ino,info.st_size,info.st_mtime_ns)
            if not stat.S_ISREG(info.st_mode) or actual != expected:
                raise RuntimeError('GitLab artifact changed')
            if operation == 'hash':
                digest=hashlib.sha256()
                while chunk := os.read(descriptor,1024*1024): digest.update(chunk)
                print(digest.hexdigest())
            elif operation == 'unlink':
                current=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
                actual=(current.st_ino,current.st_size,current.st_mtime_ns)
                if not stat.S_ISREG(current.st_mode) or actual != expected:
                    raise RuntimeError('GitLab artifact changed')
                os.unlink(name,dir_fd=rootfd)
            else:
                raise ValueError('invalid operation')
        finally:
            os.close(descriptor)
finally:
    os.close(rootfd)
""".strip()


async def _remote_file_operation(
    connection: asyncssh.SSHClientConnection,
    operation: str,
    root: str,
    state: _FileState | None,
    deadline: RunDeadline | None,
) -> str:
    encoded = base64.b64encode(_REMOTE_FILE_SCRIPT.encode()).decode()
    launcher = "import base64;exec(base64.b64decode(" + repr(encoded) + ").decode())"
    arguments = ["/usr/bin/python3", "-c", launcher, operation, root]
    if state is not None:
        arguments.extend([state.name, str(state.inode), str(state.size), str(state.mtime_ns)])
    timeout = deadline.operation_timeout(3600) if deadline is not None else 3600
    try:
        result = await connection.run(shlex.join(arguments), check=True, timeout=timeout)
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        if deadline is not None:
            deadline.check()
        raise PipelineError(
            "GITLAB_REMOTE_OPERATION_FAILED", "remote GitLab artifact operation failed"
        ) from exc
    return result.stdout.strip() if isinstance(result.stdout, str) else ""


async def _remote_snapshot(
    connection: asyncssh.SSHClientConnection,
    root: str,
    deadline: RunDeadline | None,
) -> dict[str, _FileState]:
    raw = await _remote_file_operation(connection, "snapshot", root, None, deadline)
    try:
        items = json.loads(raw)
        states = {
            str(item[0]): _FileState(str(item[0]), int(item[1]), int(item[2]), int(item[3]))
            for item in items
        }
    except (TypeError, ValueError, json.JSONDecodeError, IndexError) as exc:
        raise PipelineError(
            "GITLAB_REMOTE_OPERATION_FAILED", "remote GitLab artifact listing was invalid"
        ) from exc
    if len(states) != len(items):
        raise PipelineError("GITLAB_ARTIFACT_AMBIGUOUS", "duplicate GitLab artifact names")
    return states


async def _remote_stable_file(
    connection: asyncssh.SSHClientConnection,
    root: str,
    expected: _FileState,
    settings: Settings,
    deadline: RunDeadline | None,
) -> _FileState:
    previous: _FileState | None = None
    stable = 0
    while stable < settings.GITLAB_STABILITY_CHECKS:
        current = (await _remote_snapshot(connection, root, deadline)).get(expected.name)
        if current is None or current.size <= 0:
            raise PipelineError("GITLAB_ARTIFACT_INVALID", "GitLab artifact disappeared")
        stable = stable + 1 if current == previous else 1
        previous = current
        if stable < settings.GITLAB_STABILITY_CHECKS:
            await asyncio.sleep(settings.GITLAB_STABILITY_INTERVAL_SECONDS)
    if current is None:
        raise PipelineError("GITLAB_ARTIFACT_INVALID", "GitLab artifact is unavailable")
    return current


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PipelineError("GITLAB_ACQUISITION_FAILED", "acquired artifact is not a file")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return info.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


async def _invoke_remote_backup(
    connection: asyncssh.SSHClientConnection,
    container_name: str,
    deadline: RunDeadline | None,
) -> None:
    # container_name has already passed the strict allowlist validator. No user-provided
    # command or option is accepted here.
    container_name = validate_docker_container_name(container_name)
    command = shlex.join(("docker", "exec", "-t", container_name, "gitlab-backup", "create"))
    timeout = deadline.operation_timeout(3600) if deadline is not None else 3600
    try:
        await connection.run(command, check=True, timeout=timeout)
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        if deadline is not None:
            deadline.check()
        raise PipelineError("GITLAB_BACKUP_FAILED", "native GitLab backup command failed") from exc


async def _acquire_remote_gitlab(
    source: GitLabSource,
    destination: Path,
    settings: Settings,
    cipher: SecretCipher,
    deadline: RunDeadline | None,
) -> SourceArtifact:
    GitLabConfig(
        source.location, source.container_name, source.backup_path, source.remote_server_id
    ).validate()
    root = _validate_remote_path(source.backup_path)
    if root == "/" or PurePosixPath(root).name in {".", ".."}:
        raise PipelineError("GITLAB_ROOT_INVALID", "remote GitLab backup root is unsafe")
    server = source.remote_server
    if server is None or not server.enabled or server.id != source.remote_server_id:
        raise PipelineError("INVALID_PROFILE", "enabled remote server is missing")
    credentials = _ssh_credentials(server, cipher)
    material = destination.parent / "gitlab-ssh-material"
    material.mkdir(mode=0o700, parents=True)
    target = destination.with_suffix(".gitlab.tar")
    budget = SourceBudget(settings, destination.parent, deadline=deadline)
    try:
        connect_timeout = deadline.operation_timeout(30) if deadline is not None else 30
        try:
            context = SSHGateway().pinned_connection(
                credentials,
                server.host_key_fingerprint,
                connect_timeout=connect_timeout,
            )
            connection = await context.__aenter__()
        except SSHConnectionTimeout as exc:
            raise PipelineError("SSH_CONNECT_TIMEOUT", "SSH connection timed out") from exc
        except asyncssh.PermissionDenied as exc:
            raise PipelineError(
                "REMOTE_AUTHENTICATION_FAILED", "remote SSH authentication failed"
            ) from exc
        except HostKeyChangedError as exc:
            raise PipelineError(
                "REMOTE_HOST_KEY_MISMATCH", "remote SSH host key verification failed"
            ) from exc
        try:
            host_key = connection.get_server_host_key()
            if host_key is None:
                raise PipelineError("SSH_HOST_KEY_MISSING", "SSH server key is unavailable")
            before = await _remote_snapshot(connection, root, deadline)
            await _invoke_remote_backup(connection, source.container_name, deadline)
            detected = detect_new_backup(
                before,
                await _remote_snapshot(connection, root, deadline),
                0,
            )
            stable = await _remote_stable_file(connection, root, detected, settings, deadline)
            remote_path = str(PurePosixPath(root) / stable.name)
            remote_checksum = await _remote_file_operation(
                connection, "hash", root, stable, deadline
            )
            await _rsync_download(
                server,
                credentials,
                host_key,
                remote_path,
                target,
                material,
                budget,
                deadline,
            )
            size, checksum = await asyncio.to_thread(_hash_file, target)
            if size != stable.size or checksum != remote_checksum:
                target.unlink(missing_ok=True)
                raise PipelineError(
                    "GITLAB_ACQUISITION_FAILED", "GitLab artifact transfer failed validation"
                )
            # Re-verify the remote inode and metadata immediately before deleting it. This
            # operation is reached only after the local size and SHA-256 checks succeed.
            await _remote_file_operation(connection, "unlink", root, stable, deadline)
        finally:
            await context.__aexit__(None, None, None)
    finally:
        shutil.rmtree(material, ignore_errors=True)
    return SourceArtifact(
        target,
        ".gitlab.tar",
        {
            "kind": "gitlab",
            "location": "remote",
            "native_filename": stable.name,
            "native_size": size,
            "native_sha256": checksum,
            "source_cleanup": "succeeded",
            "verified": True,
        },
        size,
    )


async def acquire_gitlab(
    source: GitLabSource,
    destination: Path,
    settings: Settings,
    deadline: RunDeadline | None,
    cipher: SecretCipher | None = None,
) -> SourceArtifact:
    if source.location is SourceLocation.REMOTE_SERVER:
        if cipher is None:
            raise PipelineError("INVALID_PROFILE", "secret cipher is unavailable")
        return await _acquire_remote_gitlab(source, destination, settings, cipher, deadline)
    if source.location is not SourceLocation.LOCAL:
        raise PipelineError("INVALID_PROFILE", "GitLab source location is invalid")
    return await _acquire_local_gitlab(source, destination, settings, deadline)
