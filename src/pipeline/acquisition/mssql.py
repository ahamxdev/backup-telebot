import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import asyncssh
import structlog

from database.models import DatabaseSource
from domain.enums import TLSMode
from infrastructure.ssh import HostKeyChangedError, SSHConnectionTimeout
from infrastructure.ssh import SSHGateway as SSHGateway
from pipeline.acquisition.local_files import (
    _open_local_root as _open_local_root,
)
from pipeline.acquisition.local_files import (
    _open_verified_at as _open_verified_at,
)
from pipeline.acquisition.local_files import (
    _same_inode,
)
from pipeline.acquisition.remote_files import (
    _rsync_download as _rsync_download,
)
from pipeline.acquisition.remote_files import (
    _ssh_credentials,
    _validate_remote_rsync,
)
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import run_process as run_process
from security import SecretCipher

_ssh_gateway_factory: type[SSHGateway] = SSHGateway
log = structlog.get_logger()


def _sql_identifier(value: str) -> str:
    return "[" + value.replace("]", "]]") + "]"


def _sql_literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


@dataclass(frozen=True)
class MSSQLTLSConfig:
    cafile: Path | None
    validate_host: bool


def _mssql_tls_config(
    mode: TLSMode, ca_file: Path | None, *, openssl_available: bool
) -> MSSQLTLSConfig:
    if mode is TLSMode.DISABLED:
        return MSSQLTLSConfig(cafile=None, validate_host=False)
    if mode in {TLSMode.PREFERRED, TLSMode.REQUIRED}:
        raise PipelineError(
            "MSSQL_TLS_MODE_UNSUPPORTED",
            f"python-tds cannot safely implement MSSQL TLS mode {mode.value}; "
            "use DISABLED, VERIFY_CA, or VERIFY_FULL",
        )
    if ca_file is None:
        raise PipelineError(
            "MSSQL_TLS_CA_MISSING",
            f"MSSQL TLS mode {mode.value} requires a CA certificate",
        )
    if not openssl_available:
        raise PipelineError(
            "MSSQL_TLS_UNAVAILABLE",
            "python-tds TLS support is unavailable because pyOpenSSL could not be loaded",
        )
    return MSSQLTLSConfig(
        cafile=ca_file,
        validate_host=mode is TLSMode.VERIFY_FULL,
    )


def _mssql_backup(source: DatabaseSource, password: str) -> dict[str, Any]:
    import OpenSSL.SSL
    import pytds
    import pytds.tls

    if source.mssql_backup_path is None:
        raise PipelineError("INVALID_PROFILE", "MSSQL backup destination is missing")
    backup = (
        f"BACKUP DATABASE {_sql_identifier(source.database_name)} "
        f"TO DISK = {_sql_literal(source.mssql_backup_path)} "
        "WITH INIT, COPY_ONLY, CHECKSUM, COMPRESSION"
    )
    ca_file: Path | None = None
    try:
        if source.tls_mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL}:
            if not source.tls_ca_certificate:
                raise PipelineError(
                    "MSSQL_TLS_CA_MISSING",
                    f"MSSQL TLS mode {source.tls_mode.value} requires a CA certificate",
                )
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as stream:
                stream.write(source.tls_ca_certificate)
                ca_file = Path(stream.name)
            ca_file.chmod(0o600)
        tls = _mssql_tls_config(
            source.tls_mode,
            ca_file,
            openssl_available=pytds.tls.OPENSSL_AVAILABLE,
        )
        try:
            with pytds.connect(
                server=source.host,
                port=source.port,
                database="master",
                user=source.username,
                password=password,
                autocommit=True,
                timeout=60,
                login_timeout=30,
                cafile=str(tls.cafile) if tls.cafile is not None else None,
                validate_host=tls.validate_host,
            ) as connection:
                cursor = connection.cursor()
                native_compression = True
                try:
                    cursor.execute(backup)
                except pytds.Error:
                    native_compression = False
                    cursor.execute(backup.removesuffix(", COMPRESSION"))
                while cursor.nextset():
                    pass
                cursor.execute(
                    f"RESTORE VERIFYONLY FROM DISK = {_sql_literal(source.mssql_backup_path)} "
                    "WITH CHECKSUM"
                )
                while cursor.nextset():
                    pass
        except OpenSSL.SSL.Error:
            raise PipelineError(
                "MSSQL_TLS_CA_INVALID",
                "MSSQL TLS CA certificate could not be loaded or validated",
            ) from None
        except ValueError:
            raise PipelineError(
                "MSSQL_TLS_UNAVAILABLE",
                "python-tds could not initialize MSSQL TLS support",
            ) from None
        except pytds.Error:
            raise PipelineError(
                "MSSQL_BACKUP_FAILED",
                "MSSQL connection, backup, or verification failed",
            ) from None
    finally:
        if ca_file is not None:
            ca_file.unlink(missing_ok=True)
    return {
        "engine": "mssql",
        "backup_type": "FULL",
        "native_compression": native_compression,
        "checksum": True,
    }


async def _mssql(
    source: DatabaseSource,
    target: Path,
    password: str,
    cipher: SecretCipher,
    deadline: RunDeadline | None = None,
    ownership_check: Callable[[], Awaitable[None]] | None = None,
    operation_id: str | None = None,
    retrieval_record: Callable[[int, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    backup_path = source.mssql_backup_path
    if backup_path is not None and operation_id is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise PipelineError("INVALID_FENCE", "MSSQL execution fence is invalid")
        backup_path = _mssql_execution_path(backup_path, operation_id)
    request = {
        "host": source.host,
        "port": source.port,
        "database_name": source.database_name,
        "username": source.username,
        "password": password,
        "tls_mode": source.tls_mode.value,
        "tls_ca_certificate": source.tls_ca_certificate,
        "mssql_backup_path": backup_path,
    }
    try:
        result = await run_process(
            # Keep the virtual-environment interpreter path. Resolving this
            # symlink selects the base interpreter and loses the installed
            # application environment in the production image.
            Path(sys.executable),
            ("-m", "pipeline.mssql_runner"),
            timeout=deadline.remaining() if deadline is not None else 21600,
            stdin_data=json.dumps(request).encode(),
        )
    except PipelineError as exc:
        if exc.code == "PROCESS_TIMEOUT":
            raise PipelineError("MSSQL_TIMEOUT", "MSSQL backup timed out") from None
        raise
    try:
        response = json.loads(result.stdout)
        if not response.get("ok"):
            raise PipelineError(str(response["code"]), str(response["message"]))
        metadata = cast(dict[str, Any], response["metadata"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError("MSSQL_BACKUP_FAILED", "MSSQL backup process failed") from exc
    if ownership_check is not None:
        await ownership_check()
    visible = source.mssql_host_backup_path if source.mssql_in_docker else source.mssql_backup_path
    if visible is not None and operation_id is not None:
        visible = _mssql_execution_path(visible, operation_id)
    if visible is None:
        raise PipelineError("MSSQL_ARTIFACT_MISSING", "MSSQL backup path is not visible")
    server = source.artifact_remote_server
    if server is not None:
        if not server.enabled or server.deleted_at is not None:
            raise PipelineError("MSSQL_REMOTE_DISABLED", "MSSQL artifact server is disabled")
        material = Path(tempfile.mkdtemp(prefix=".ssh-material-", dir=target.parent))
        credentials = _ssh_credentials(server, cipher)
        log.info("remote_acquisition_started", method="rsync")
        try:
            try:
                async with _ssh_gateway_factory().pinned_connection(
                    credentials,
                    server.host_key_fingerprint,
                    connect_timeout=(
                        deadline.operation_timeout(30) if deadline is not None else 30
                    ),
                ) as connection:
                    await _validate_remote_rsync(connection, deadline)
                    host_key = connection.get_server_host_key()
                    if host_key is None:
                        raise PipelineError("SSH_HOST_KEY_MISSING", "SSH server key is unavailable")
                    size, count = await _rsync_download(
                        server,
                        credentials,
                        host_key,
                        visible,
                        target,
                        material,
                        None,
                        deadline,
                    )
            except asyncssh.PermissionDenied as exc:
                raise PipelineError(
                    "REMOTE_AUTHENTICATION_FAILED", "remote SSH authentication failed"
                ) from exc
            except SSHConnectionTimeout as exc:
                if deadline is not None:
                    deadline.check()
                raise PipelineError("SSH_CONNECT_TIMEOUT", "SSH connection timed out") from exc
            except HostKeyChangedError as exc:
                raise PipelineError(
                    "REMOTE_HOST_KEY_MISMATCH", "remote SSH host key verification failed"
                ) from exc
            log.info(
                "remote_rsync_completed",
                files_count=count,
                bytes_transferred=size,
            )
        except Exception as exc:
            error_code = exc.code if isinstance(exc, PipelineError) else "REMOTE_ACQUISITION_FAILED"
            log.error("remote_acquisition_failed", error_code=error_code)
            raise
        finally:
            shutil.rmtree(material, ignore_errors=True)
    else:
        origin = Path(visible).resolve(strict=True)
        if not origin.is_file():
            raise PipelineError("MSSQL_ARTIFACT_MISSING", "MSSQL backup artifact is unavailable")
        stop = CooperativeStop(deadline.check if deadline is not None else None)
        await run_cooperative_thread(
            lambda: _copy_local_artifact(origin, target, deadline, stop.check), stop
        )
    if target.stat().st_size == 0:
        raise PipelineError("EMPTY_BACKUP", "MSSQL backup is empty")
    if ownership_check is not None:
        await ownership_check()
    hash_stop = CooperativeStop(deadline.check if deadline is not None else None)
    retrieved_sha256 = await run_cooperative_thread(
        lambda: _hash_open_path(target, hash_stop.check), hash_stop
    )
    if ownership_check is not None:
        await ownership_check()
    if retrieval_record is not None:
        await retrieval_record(target.stat().st_size, retrieved_sha256)
    return metadata


def _mssql_execution_path(path: str, operation_id: str) -> str:
    suffix = ".bak" if path.lower().endswith(".bak") else ""
    stem = path[: -len(suffix)] if suffix else path
    return f"{stem}.{operation_id}{suffix}"


def _mssql_visible_execution_path(source: DatabaseSource, operation_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise PipelineError("MSSQL_CLEANUP_REFUSED", "MSSQL cleanup fence is invalid")
    base = source.mssql_host_backup_path if source.mssql_in_docker else source.mssql_backup_path
    if base is None or not base.lower().endswith(".bak"):
        raise PipelineError(
            "MSSQL_CLEANUP_REFUSED", "MSSQL cleanup requires a configured .bak path"
        )
    normalized = PurePosixPath(base)
    if not normalized.is_absolute() or ".." in normalized.parts or str(normalized) != base:
        raise PipelineError("MSSQL_CLEANUP_REFUSED", "MSSQL cleanup path is unsafe")
    return _mssql_execution_path(base, operation_id)


def _hash_open_file(
    descriptor: int,
    check: Callable[[], None] | None = None,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if check is not None:
                check()
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _hash_open_path(path: Path, check: Callable[[], None] | None = None) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            if check is not None:
                check()
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_local_mssql_artifact(
    artifact_path: str,
    expected_size: int,
    expected_sha256: str,
    check: Callable[[], None] | None = None,
) -> None:
    path = Path(artifact_path)
    parent_fd, parent_stat = _open_local_root(path.parent)
    try:
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise PipelineError("MSSQL_CLEANUP_REFUSED", "MSSQL cleanup parent is invalid")
        try:
            entry_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise PipelineError(
                "MSSQL_CLEANUP_MISSING", "MSSQL server artifact is unavailable"
            ) from error
        if not stat.S_ISREG(entry_stat.st_mode):
            raise PipelineError(
                "MSSQL_CLEANUP_REFUSED", "MSSQL server artifact is not a regular file"
            )
        artifact_fd, opened_stat = _open_verified_at(
            parent_fd, path.name, entry_stat, directory=False
        )
        try:
            actual_size, actual_sha256 = _hash_open_file(artifact_fd, check)
            if actual_size != expected_size or actual_sha256 != expected_sha256:
                raise PipelineError(
                    "MSSQL_CLEANUP_MISMATCH",
                    "MSSQL server artifact no longer matches the retrieved backup",
                )
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not _same_inode(opened_stat, current):
                raise PipelineError(
                    "MSSQL_CLEANUP_REFUSED", "MSSQL server artifact changed before cleanup"
                )
            os.unlink(path.name, dir_fd=parent_fd)
        finally:
            os.close(artifact_fd)
    finally:
        os.close(parent_fd)


async def _cleanup_remote_mssql_artifact(
    source: DatabaseSource,
    artifact_path: str,
    expected_size: int,
    expected_sha256: str,
    cipher: SecretCipher,
    deadline: RunDeadline | None,
) -> None:
    server = source.artifact_remote_server
    if (
        server is None
        or server.id != source.artifact_remote_server_id
        or not server.enabled
        or server.deleted_at is not None
    ):
        raise PipelineError(
            "MSSQL_CLEANUP_REMOTE_UNAVAILABLE",
            "trusted MSSQL artifact server is unavailable",
        )
    timeout = deadline.operation_timeout(30) if deadline is not None else 30
    script = """import hashlib, os, stat, sys
path, expected_size, expected_digest = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except FileNotFoundError:
    raise SystemExit(2)
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit(4)
    digest = hashlib.sha256()
    size = 0
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    if size != expected_size or digest.hexdigest() != expected_digest:
        raise SystemExit(3)
    current = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
        before.st_dev,
        before.st_ino,
    ):
        raise SystemExit(4)
    os.unlink(path)
finally:
    os.close(descriptor)
"""
    encoded = base64.b64encode(script.encode()).decode()
    launcher = "import base64;exec(base64.b64decode(" + repr(encoded) + ").decode())"
    command = shlex.join(
        ("/usr/bin/python3", "-c", launcher, artifact_path, str(expected_size), expected_sha256)
    )
    try:
        async with _ssh_gateway_factory().pinned_connection(
            _ssh_credentials(server, cipher),
            server.host_key_fingerprint,
            connect_timeout=timeout,
        ) as connection:
            result = await connection.run(
                command, timeout=deadline.operation_timeout(60) if deadline else 60
            )
    except TimeoutError as exc:
        raise PipelineError("MSSQL_CLEANUP_TIMEOUT", "MSSQL artifact cleanup timed out") from exc
    codes = {
        2: ("MSSQL_CLEANUP_MISSING", "MSSQL server artifact is unavailable"),
        3: (
            "MSSQL_CLEANUP_MISMATCH",
            "MSSQL server artifact no longer matches the retrieved backup",
        ),
        4: ("MSSQL_CLEANUP_REFUSED", "MSSQL server artifact changed before cleanup"),
    }
    if result.exit_status != 0:
        code, message = codes.get(
            result.exit_status or -1,
            ("MSSQL_CLEANUP_FAILED", "MSSQL server artifact cleanup failed"),
        )
        raise PipelineError(code, message)


async def cleanup_mssql_server_artifact(
    source: DatabaseSource,
    artifact_path: str,
    operation_id: str,
    expected_size: int,
    expected_sha256: str,
    cipher: SecretCipher,
    deadline: RunDeadline | None = None,
) -> None:
    expected_path = _mssql_visible_execution_path(source, operation_id)
    if artifact_path != expected_path:
        raise PipelineError(
            "MSSQL_CLEANUP_REFUSED", "MSSQL cleanup path does not match its execution fence"
        )
    if expected_size <= 0 or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise PipelineError(
            "MSSQL_CLEANUP_REFUSED", "MSSQL cleanup verification metadata is invalid"
        )
    if source.artifact_remote_server_id is not None:
        await _cleanup_remote_mssql_artifact(
            source,
            artifact_path,
            expected_size,
            expected_sha256,
            cipher,
            deadline,
        )
        return
    stop = CooperativeStop(deadline.check if deadline is not None else None)
    await run_cooperative_thread(
        lambda: _cleanup_local_mssql_artifact(
            artifact_path, expected_size, expected_sha256, stop.check
        ),
        stop,
    )


def _copy_local_artifact(
    source: Path,
    target: Path,
    deadline: RunDeadline | None = None,
    stop_check: Callable[[], None] | None = None,
) -> None:
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        while chunk := input_stream.read(1024 * 1024):
            if deadline is not None:
                deadline.check()
            if stop_check is not None:
                stop_check()
            output_stream.write(chunk)
