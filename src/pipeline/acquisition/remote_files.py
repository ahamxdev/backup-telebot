import base64
import os
import re
import shlex
import shutil
import stat
import tarfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path, PurePosixPath

import asyncssh
import structlog

from config.settings import Settings
from database.models import BackupProfile, RemoteServer
from infrastructure.ssh import (
    HostKeyChangedError,
    SSHConnectionTimeout,
    SSHCredentials,
)
from infrastructure.ssh import (
    SSHGateway as SSHGateway,
)
from pipeline.acquisition.common import SourceBudget
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import ProcessExecutionError
from pipeline.process import run_process as run_process
from security import SecretCipher

_ssh_gateway_factory: type[SSHGateway] = SSHGateway
_RSYNC_LOG_OUTPUT_LIMIT = 16_384
log = structlog.get_logger()


def _ssh_credentials(server: RemoteServer, cipher: SecretCipher) -> SSHCredentials:
    passphrase = None
    if server.encrypted_key_passphrase:
        passphrase = cipher.decrypt(
            server.encrypted_key_passphrase, context="remote-server-key-passphrase-v1"
        )
    return SSHCredentials(
        server.host,
        server.ssh_port,
        server.ssh_username,
        cipher.decrypt(server.encrypted_private_key, context="remote-server-private-key-v1"),
        passphrase,
    )


def _confined_download_destination(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PipelineError(
            "UNSAFE_SOURCE", "remote source destination escapes the run directory"
        ) from exc
    return resolved_candidate


def _validate_remote_path(value: str) -> str:
    """Validate persisted remote paths again at the worker trust boundary."""
    if not value or "\x00" in value or any(ord(character) < 32 for character in value):
        raise PipelineError("UNSAFE_SOURCE", "remote source path is unsafe")
    path = PurePosixPath(value)
    normalized = str(path)
    trailing_slash = value.endswith("/") and value != "/"
    expected = normalized + "/" if trailing_slash else normalized
    if not path.is_absolute() or ".." in path.parts or value != expected:
        raise PipelineError("UNSAFE_SOURCE", "remote source path is unsafe")
    return value


def _tar_download(
    source: Path,
    destination: Path,
    capacity_check: Callable[[], None] | None = None,
) -> None:
    def checked(member: tarfile.TarInfo) -> tarfile.TarInfo:
        if capacity_check is not None:
            capacity_check()
        return member

    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        archive.add(source, arcname="sources", recursive=True, filter=checked)


def _verify_tar(
    path: Path,
    deadline: RunDeadline | None = None,
    stop_check: Callable[[], None] | None = None,
) -> None:
    with tarfile.open(path, "r") as archive:
        for member in archive:
            if deadline is not None:
                deadline.check()
            if stop_check is not None:
                stop_check()
            member_path = PurePosixPath(member.name)
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise PipelineError("UNSAFE_ARCHIVE", "source archive contains unsafe entries")


async def _remote_sqlite_snapshot(
    connection: asyncssh.SSHClientConnection,
    server: RemoteServer,
    credentials: SSHCredentials,
    host_key: asyncssh.SSHKey,
    source: str,
    local: Path,
    material: Path,
    budget: SourceBudget,
    deadline: RunDeadline | None = None,
) -> None:
    # The 122-bit random name is deleted in a finally block on the pinned remote host.
    remote = f"/tmp/backup-telebot-{uuid.uuid4()}.sqlite"  # noqa: S108  # nosec B108
    script = (
        "import sqlite3,sys;"
        "a=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);"
        "b=sqlite3.connect(sys.argv[2]);a.backup(b);"
        "r=b.execute('PRAGMA integrity_check').fetchone();"
        "a.close();b.close();assert r==('ok',),'integrity check failed'"
    )
    encoded = base64.b64encode(script.encode()).decode()
    launcher = "import base64;exec(base64.b64decode(" + repr(encoded) + ").decode())"
    command = shlex.join(("/usr/bin/python3", "-c", launcher, source, remote))
    try:
        timeout = deadline.operation_timeout(3600) if deadline is not None else 3600
        await connection.run(command, check=True, timeout=timeout)
        await _rsync_download(
            server,
            credentials,
            host_key,
            remote,
            local,
            material,
            budget,
            deadline,
        )
    finally:
        with suppress(OSError, asyncssh.Error):
            cleanup_timeout = deadline.operation_timeout(60) if deadline is not None else 60
            await connection.run(
                shlex.join(("/bin/rm", "-f", "--", remote)),
                check=False,
                timeout=cleanup_timeout,
            )


def _validate_download(
    path: Path,
    budget: SourceBudget | None = None,
    *,
    commit: bool = False,
) -> tuple[int, int]:
    bytes_found = 0
    files_found = 0
    if not path.exists():
        if budget is not None:
            budget.check_disk()
        return 0, 0
    if path.is_file():
        size = path.stat().st_size
        if budget is not None:
            budget.validate_file_size(size)
            budget.validate_addition(size, 1)
            budget.check_disk()
            if commit:
                budget.commit_scanned(size, 1)
        return size, 1
    for current, directories, files in os.walk(path, followlinks=False):
        base = Path(current)
        if budget is not None:
            budget.check_depth(len(base.relative_to(path).parts))
        for name in directories + files:
            item = base / name
            if item.is_symlink():
                raise PipelineError("UNSAFE_SOURCE", "remote source contains a symbolic link")
            mode = item.stat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise PipelineError("UNSAFE_SOURCE", "remote source contains a special file")
            if stat.S_ISREG(mode):
                size = item.stat().st_size
                if budget is not None:
                    budget.validate_file_size(size)
                bytes_found += size
                files_found += 1
        if budget is not None:
            budget.validate_addition(bytes_found, files_found)
            budget.check_disk()
    if budget is not None and commit:
        budget.commit_scanned(bytes_found, files_found)
    return bytes_found, files_found


def _remove_download(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _classify_rsync_failure(error: ProcessExecutionError) -> tuple[str, str]:
    """Map bounded rsync output to a fixed reason which is safe to persist and log."""
    diagnostic = f"{error.stderr}\n{error.stdout}".casefold()
    if "permission denied" in diagnostic or "operation not permitted" in diagnostic:
        return "RSYNC_PERMISSION_DENIED", "rsync permission denied"
    missing_markers = (
        "no such file or directory",
        "link_stat ",
        "change_dir ",
        "failed to stat ",
    )
    if any(marker in diagnostic for marker in missing_markers):
        return "RSYNC_REMOTE_PATH_NOT_FOUND", "remote rsync path not found"
    timeout_markers = ("connection timed out", "operation timed out", "timeout waiting")
    if error.exit_code in {30, 35} or any(marker in diagnostic for marker in timeout_markers):
        return "RSYNC_TIMEOUT", "rsync transfer timed out"
    connection_markers = (
        "connection refused",
        "connection reset",
        "connection unexpectedly closed",
        "could not resolve hostname",
        "host key verification failed",
        "no route to host",
        "ssh_exchange_identification",
    )
    if error.exit_code in {5, 10, 12, 255} or any(
        marker in diagnostic for marker in connection_markers
    ):
        return "RSYNC_CONNECTION_FAILED", "rsync SSH connection failed"
    return "RSYNC_TRANSFER_FAILED", "rsync transfer failed"


def _sanitize_rsync_output(value: str, replacements: tuple[str, ...]) -> str:
    sanitized = value
    for sensitive in sorted((item for item in replacements if item), key=len, reverse=True):
        sanitized = sanitized.replace(sensitive, "[REDACTED]")
    for candidate in re.findall(r"/[A-Za-z0-9._~!$&()+,;=:@%/-]+", sanitized):
        if re.search(r"(?:credential|key|pass|secret|token)", candidate, re.IGNORECASE):
            sanitized = sanitized.replace(candidate, "[REDACTED_PATH]")
    return " ".join(sanitized.split())[-_RSYNC_LOG_OUTPUT_LIMIT:]


async def _rsync_download(
    server: RemoteServer,
    credentials: SSHCredentials,
    host_key: asyncssh.SSHKey,
    remote: str,
    local: Path,
    material: Path,
    budget: SourceBudget | None,
    deadline: RunDeadline | None = None,
) -> tuple[int, int]:
    rsync_executable = Path("/usr/bin/rsync")
    if not rsync_executable.is_file() or not os.access(rsync_executable, os.X_OK):
        raise PipelineError("REMOTE_RSYNC_UNAVAILABLE", "local rsync execution failed")
    private_key, known_hosts = material / "id", material / "known_hosts"
    key = asyncssh.import_private_key(credentials.private_key, credentials.passphrase)
    private_key.write_bytes(key.export_private_key("openssh"))
    private_key.chmod(0o600)
    host = f"[{server.host}]:{server.ssh_port}" if server.ssh_port != 22 else server.host
    known_hosts.write_bytes(host.encode() + b" " + host_key.export_public_key("openssh"))
    known_hosts.chmod(0o600)
    # rsync accepts its remote-shell program as one argument. Build that nested
    # argv with shlex.join so paths are quoted as data, while the rsync process
    # itself is always launched directly by create_subprocess_exec().
    ssh = shlex.join(
        (
            "/usr/bin/ssh",
            "-i",
            str(private_key),
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-p",
            str(server.ssh_port),
        )
    )
    host_spec = f"[{server.host}]" if ":" in server.host else server.host
    remote = _validate_remote_path(remote)
    rsync_args = (
        "-aHAX",
        "--numeric-ids",
        "--safe-links",
        "--no-specials",
        "--no-devices",
        "--secluded-args",
        "-e",
        ssh,
        "--",
        f"{server.ssh_username}@{host_spec}:{remote}",
        str(local),
    )
    sanitized_ssh = shlex.join(
        (
            "/usr/bin/ssh",
            "-i",
            "[REDACTED_IDENTITY_FILE]",
            "-o",
            "UserKnownHostsFile=[REDACTED_KNOWN_HOSTS_FILE]",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-p",
            str(server.ssh_port),
        )
    )
    sanitized_args = (*rsync_args[:-4], sanitized_ssh, "--", f"{host_spec}:{remote}", str(local))
    output_replacements = (
        credentials.private_key,
        credentials.passphrase or "",
        str(private_key),
        str(known_hosts),
        f"{server.ssh_username}@{host_spec}",
    )

    def monitor_download() -> None:
        _validate_download(local, budget)

    log.info(
        "remote_rsync_started",
        executable=str(rsync_executable),
        arguments=sanitized_args,
        remote_source=remote,
        local_destination=str(local),
    )
    try:
        result = await run_process(
            rsync_executable,
            rsync_args,
            timeout=deadline.operation_timeout(3600) if deadline is not None else 3600,
            capacity_check=monitor_download,
        )
    except ProcessExecutionError as exc:
        error_code, reason = _classify_rsync_failure(exc)
        log.error(
            "remote_rsync_failed",
            error_code=error_code,
            exit_code=exc.exit_code,
            reason=reason,
            stdout=_sanitize_rsync_output(exc.stdout, output_replacements),
            stderr=_sanitize_rsync_output(exc.stderr, output_replacements),
        )
        raise PipelineError(error_code, reason) from exc
    except PipelineError as exc:
        if exc.code == "PROCESS_TIMEOUT":
            reason = "rsync transfer timed out"
            log.error(
                "remote_rsync_failed",
                error_code="RSYNC_TIMEOUT",
                exit_code=None,
                reason=reason,
                stdout="",
                stderr="",
            )
            raise PipelineError("RSYNC_TIMEOUT", reason) from exc
        if exc.code == "PROCESS_FAILED":
            reason = "rsync transfer failed"
            log.error(
                "remote_rsync_failed",
                error_code="RSYNC_TRANSFER_FAILED",
                exit_code=None,
                reason=reason,
                stdout="",
                stderr="",
            )
            raise PipelineError("RSYNC_TRANSFER_FAILED", reason) from exc
        raise
    log.info(
        "remote_rsync_process_completed",
        return_code=0,
        stdout=_sanitize_rsync_output(result.stdout.decode(errors="replace"), output_replacements),
        stderr=_sanitize_rsync_output(result.stderr, output_replacements),
    )
    return _validate_download(local, budget, commit=True)


async def _validate_remote_rsync(
    connection: asyncssh.SSHClientConnection,
    deadline: RunDeadline | None = None,
) -> str:
    timeout = deadline.operation_timeout(30) if deadline is not None else 30
    try:
        result = await connection.run("command -v rsync", timeout=timeout)
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        if deadline is not None:
            deadline.check()
        raise PipelineError("REMOTE_SSH_CHECK_FAILED", "remote SSH validation failed") from exc
    stdout = result.stdout
    executable = stdout.strip() if isinstance(stdout, str) else ""
    path = PurePosixPath(executable)
    if (
        result.exit_status != 0
        or not executable
        or any(ord(character) < 32 for character in executable)
        or not path.is_absolute()
        or ".." in path.parts
        or str(path) != executable
        or path.name != "rsync"
    ):
        raise PipelineError("REMOTE_RSYNC_MISSING", "rsync is not installed on the remote server")
    log.info("remote_rsync_available", path=executable)
    return executable


async def _package_remote(
    profile: BackupProfile,
    destination: Path,
    cipher: SecretCipher,
    settings: Settings,
    deadline: RunDeadline | None = None,
) -> int:
    source = profile.files_source
    if source is None or source.remote_server is None or not source.remote_server.enabled:
        raise PipelineError("INVALID_PROFILE", "enabled remote server is missing")
    server = source.remote_server
    download_root = destination.parent / "remote-download"
    download_root.mkdir(mode=0o700)
    material = destination.parent / "ssh-material"
    material.mkdir(mode=0o700)
    budget = SourceBudget(settings, destination.parent, deadline=deadline)
    budget.check_disk()
    credentials = _ssh_credentials(server, cipher)
    log.info("remote_acquisition_started", method="rsync")
    try:
        connect_timeout = deadline.operation_timeout(30) if deadline is not None else 30
        try:
            connection_context = _ssh_gateway_factory().pinned_connection(
                credentials,
                server.host_key_fingerprint,
                connect_timeout=connect_timeout,
            )
            connection = await connection_context.__aenter__()
        except SSHConnectionTimeout as exc:
            if deadline is not None:
                deadline.check()
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
            await _validate_remote_rsync(connection, deadline)
            files_count = 0
            bytes_transferred = 0
            for index, item in enumerate(source.paths):
                local = download_root / str(index)
                _confined_download_destination(download_root, local)
                remote_path = _validate_remote_path(item.path)
                if item.sqlite_snapshot_candidate:
                    await _remote_sqlite_snapshot(
                        connection,
                        server,
                        credentials,
                        host_key,
                        remote_path,
                        local,
                        material,
                        budget,
                        deadline,
                    )
                    size, count = _validate_download(local)
                    bytes_transferred += size
                    files_count += count
                    continue
                size, count = await _rsync_download(
                    server,
                    credentials,
                    host_key,
                    remote_path,
                    local,
                    material,
                    budget,
                    deadline,
                )
                bytes_transferred += size
                files_count += count
        finally:
            await connection_context.__aexit__(None, None, None)
        budget.check_disk(budget.bytes_used + 4096)
        stop = CooperativeStop(budget.check_disk)
        await run_cooperative_thread(
            lambda: _tar_download(download_root, destination, stop.check), stop
        )
        budget.check_disk()
        log.info(
            "remote_rsync_completed",
            files_count=files_count,
            bytes_transferred=bytes_transferred,
        )
        return budget.bytes_used
    except asyncssh.ConnectionLost as exc:
        log.error(
            "remote_acquisition_failed",
            error_code="SSH_CONNECTION_LOST",
            reason="SSH connection lost",
        )
        raise PipelineError("SSH_CONNECTION_LOST", "SSH connection lost") from exc
    except Exception as exc:
        error_code = exc.code if isinstance(exc, PipelineError) else "REMOTE_ACQUISITION_FAILED"
        log.error("remote_acquisition_failed", error_code=error_code)
        raise
    finally:
        shutil.rmtree(download_root, ignore_errors=True)
        shutil.rmtree(material, ignore_errors=True)
