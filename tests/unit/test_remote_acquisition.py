import asyncio
import base64
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import asyncssh
import pytest

from config.settings import Settings
from database.models import BackupPath, BackupProfile, FilesSource, RemoteServer
from domain.enums import SourceLocation, SourceType, SSHAuthenticationMethod
from infrastructure.ssh import SSHCredentials
from pipeline.acquisition import (
    _confined_download_destination,
    _validate_remote_path,
    remote_files,
)
from pipeline.acquisition.common import SourceBudget
from pipeline.errors import PipelineError
from pipeline.process import ProcessExecutionError, ProcessResult
from pipeline.process import run_process as supervised_process
from security import SecretCipher


@pytest.mark.parametrize(
    "path",
    [
        "",
        "relative",
        "nested/file",
        "/folder/../escape",
        "/not//normalized",
        "/control\ncharacter",
        "/nul\x00character",
    ],
)
def test_remote_path_rejects_unsafe_values(path: str) -> None:
    with pytest.raises(PipelineError) as caught:
        _validate_remote_path(path)
    assert caught.value.code == "UNSAFE_SOURCE"


@pytest.mark.parametrize("path", ["/", "/srv/source", "/srv/source/", "/name with spaces"])
def test_remote_path_accepts_normalized_absolute_paths(path: str) -> None:
    assert _validate_remote_path(path) == path


def test_download_destination_rejects_lexical_escape(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    with pytest.raises(PipelineError, match="escapes the run directory"):
        _confined_download_destination(root, root / ".." / "escape")


def test_download_destination_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "run"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PipelineError, match="escapes the run directory"):
        _confined_download_destination(root, root / "redirect" / "escaped-file")


def _remote_server(private_key: str) -> RemoteServer:
    return RemoteServer(
        id=1,
        name="remote",
        host="backup.example",
        ssh_port=2222,
        ssh_username="backupbot",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=b"unused",
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
        deleted_at=None,
    )


def _remote_profile(cipher: SecretCipher, private_key: str) -> BackupProfile:
    server = _remote_server(private_key)
    server.encrypted_private_key = cipher.encrypt(
        private_key, context="remote-server-private-key-v1"
    )
    return BackupProfile(
        id=1,
        name="remote",
        source_type=SourceType.FILES_DIRECTORIES,
        files_source=FilesSource(
            profile_id=1,
            location=SourceLocation.REMOTE_SERVER,
            remote_server_id=1,
            remote_server=server,
            paths=[
                BackupPath(
                    id=1,
                    files_source_id=1,
                    path="/srv/source/",
                    sqlite_snapshot_candidate=False,
                )
            ],
        ),
    )


async def test_remote_rsync_exists_returns_executable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class Connection:
        async def run(self, command: str, **_kwargs: object) -> SimpleNamespace:
            assert command == "command -v rsync"
            return SimpleNamespace(exit_status=0, stdout="/usr/bin/rsync\n")

    class Logger:
        def info(self, event: str, **values: object) -> None:
            events.append((event, values))

    monkeypatch.setattr(remote_files, "log", Logger())

    executable = await remote_files._validate_remote_rsync(cast(Any, Connection()))

    assert executable == "/usr/bin/rsync"
    assert events == [("remote_rsync_available", {"path": "/usr/bin/rsync"})]


async def test_remote_rsync_missing_returns_safe_error() -> None:
    class Connection:
        async def run(self, command: str, **_kwargs: object) -> SimpleNamespace:
            assert command == "command -v rsync"
            return SimpleNamespace(exit_status=127, stdout="")

    with pytest.raises(PipelineError) as caught:
        await remote_files._validate_remote_rsync(cast(Any, Connection()))

    assert caught.value.code == "REMOTE_RSYNC_MISSING"
    assert str(caught.value) == "rsync is not installed on the remote server"


async def test_remote_rsync_empty_stdout_is_unavailable_even_with_success_status() -> None:
    class Connection:
        async def run(self, command: str, **_kwargs: object) -> SimpleNamespace:
            assert command == "command -v rsync"
            return SimpleNamespace(exit_status=0, stdout="")

    with pytest.raises(PipelineError) as caught:
        await remote_files._validate_remote_rsync(cast(Any, Connection()))

    assert caught.value.code == "REMOTE_RSYNC_MISSING"


async def test_remote_authentication_failure_returns_safe_error(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode()
    secret_cipher = SecretCipher(base64.b64encode(b"r" * 32).decode())
    profile = _remote_profile(secret_cipher, key)

    class Context:
        async def __aenter__(self) -> None:
            raise asyncssh.PermissionDenied("denied")

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    monkeypatch.setattr(remote_files, "_ssh_gateway_factory", Gateway)
    with pytest.raises(PipelineError) as caught:
        await remote_files._package_remote(
            profile, tmp_path / "source.tar", secret_cipher, settings
        )

    assert caught.value.code == "REMOTE_AUTHENTICATION_FAILED"
    assert not (tmp_path / "remote-download").exists()
    assert not (tmp_path / "ssh-material").exists()


async def test_successful_remote_acquisition_never_starts_sftp(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    secret_cipher = SecretCipher(base64.b64encode(b"r" * 32).decode())
    profile = _remote_profile(secret_cipher, private_key)
    connection_exited = False

    class Connection:
        async def run(self, _command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(exit_status=0, stdout="/usr/bin/rsync\n")

        def get_server_host_key(self) -> asyncssh.SSHKey:
            return host_key

        def start_sftp_client(self) -> None:
            pytest.fail("SFTP must not be used for remote acquisition")

    class Context:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal connection_exited
            connection_exited = True
            return None

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    async def successful_rsync(*args: object, **_kwargs: object) -> tuple[int, int]:
        assert not connection_exited
        local = cast(Path, args[4])
        local.mkdir(parents=True)
        (local / "payload").write_bytes(b"remote data")
        return len(b"remote data"), 1

    monkeypatch.setattr(remote_files, "_ssh_gateway_factory", Gateway)
    monkeypatch.setattr(remote_files, "_rsync_download", successful_rsync)
    destination = tmp_path / "source.tar"

    await remote_files._package_remote(
        profile,
        destination,
        secret_cipher,
        settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}),
    )

    assert destination.is_file()
    assert connection_exited
    assert not (tmp_path / "remote-download").exists()
    assert not (tmp_path / "ssh-material").exists()


async def test_asyncssh_connection_lost_is_mapped_without_integer_error_code(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    secret_cipher = SecretCipher(base64.b64encode(b"r" * 32).decode())
    profile = _remote_profile(secret_cipher, private_key)

    class Connection:
        async def run(self, _command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(exit_status=0, stdout="/usr/bin/rsync\n")

        def get_server_host_key(self) -> asyncssh.SSHKey:
            return host_key

    class Context:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            raise asyncssh.ConnectionLost("connection lost")

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    async def successful_rsync(*args: object, **_kwargs: object) -> tuple[int, int]:
        local = cast(Path, args[4])
        local.mkdir(parents=True)
        (local / "payload").write_bytes(b"remote data")
        return len(b"remote data"), 1

    monkeypatch.setattr(remote_files, "_ssh_gateway_factory", Gateway)
    monkeypatch.setattr(remote_files, "_rsync_download", successful_rsync)

    with pytest.raises(PipelineError) as caught:
        await remote_files._package_remote(
            profile,
            tmp_path / "source.tar",
            secret_cipher,
            settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}),
        )

    assert caught.value.code == "SSH_CONNECTION_LOST"
    assert str(caught.value) == "SSH connection lost"


async def test_failed_process_captures_bounded_stdout_stderr_and_exit_code() -> None:
    program = (
        "import sys;print('diagnostic out');print('diagnostic err',file=sys.stderr);sys.exit(23)"
    )

    with pytest.raises(ProcessExecutionError) as caught:
        await supervised_process(Path(sys.executable), ("-c", program), timeout=10)

    assert caught.value.exit_code == 23
    assert caught.value.stdout == "diagnostic out\n"
    assert caught.value.stderr == "diagnostic err\n"


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr", "expected_code", "expected_reason"),
    [
        (
            23,
            "",
            "rsync: [sender] send_files failed: Permission denied (13)",
            "RSYNC_PERMISSION_DENIED",
            "rsync permission denied",
        ),
        (
            23,
            "",
            'rsync: [sender] link_stat "/private/source" failed: No such file or directory (2)',
            "RSYNC_REMOTE_PATH_NOT_FOUND",
            "remote rsync path not found",
        ),
        (
            255,
            "",
            "ssh: connect to host example port 22: Connection refused",
            "RSYNC_CONNECTION_FAILED",
            "rsync SSH connection failed",
        ),
    ],
)
def test_rsync_failure_diagnostics_are_mapped_to_safe_reasons(
    exit_code: int,
    stdout: str,
    stderr: str,
    expected_code: str,
    expected_reason: str,
) -> None:
    error = ProcessExecutionError(exit_code, stdout, stderr)

    assert remote_files._classify_rsync_failure(error) == (expected_code, expected_reason)


async def test_rsync_permission_failure_logs_only_safe_diagnostics(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    server = _remote_server(private_key)
    credentials = SSHCredentials(
        server.host, server.ssh_port, server.ssh_username, private_key, None
    )
    material = tmp_path / "ssh-material"
    material.mkdir(mode=0o700)
    events: list[tuple[str, dict[str, object]]] = []

    async def failed_process(*_args: object, **_kwargs: object) -> ProcessResult:
        raise ProcessExecutionError(
            23,
            "",
            'rsync: failed to open "/srv/private/customer-secret": Permission denied (13)',
        )

    class Logger:
        def info(self, _event: str, **_values: object) -> None:
            return None

        def error(self, event: str, **values: object) -> None:
            events.append((event, values))

    monkeypatch.setattr(remote_files, "run_process", failed_process)
    monkeypatch.setattr(remote_files, "log", Logger())

    with pytest.raises(PipelineError) as caught:
        await remote_files._rsync_download(
            server,
            credentials,
            host_key,
            "/srv/source/",
            tmp_path / "remote-download" / "0",
            material,
            SourceBudget(settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}), tmp_path),
        )

    assert caught.value.code == "RSYNC_PERMISSION_DENIED"
    assert events == [
        (
            "remote_rsync_failed",
            {
                "error_code": "RSYNC_PERMISSION_DENIED",
                "exit_code": 23,
                "reason": "rsync permission denied",
                "stdout": "",
                "stderr": 'rsync: failed to open "[REDACTED_PATH]": Permission denied (13)',
            },
        )
    ]
    assert "customer-secret" not in repr(events)


async def test_rsync_acquisition_uses_key_and_strict_host_verification(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    server = _remote_server(private_key)
    credentials = SSHCredentials(
        server.host, server.ssh_port, server.ssh_username, private_key, None
    )
    material = tmp_path / "ssh-material"
    material.mkdir(mode=0o700)
    local = tmp_path / "remote-download" / "0"
    observed: dict[str, object] = {}
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_run_process(
        executable: Path, args: tuple[str, ...], **kwargs: object
    ) -> ProcessResult:
        observed.update(executable=executable, args=args, kwargs=kwargs)
        local.mkdir(parents=True)
        (local / "payload").write_bytes(b"remote data")
        return ProcessResult(b"rsync stdout\n", "rsync stderr\n")

    class Logger:
        def info(self, event: str, **values: object) -> None:
            events.append((event, values))

    monkeypatch.setattr(remote_files, "run_process", fake_run_process)
    monkeypatch.setattr(remote_files, "log", Logger())
    budget = SourceBudget(settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}), tmp_path)

    await remote_files._rsync_download(
        server,
        credentials,
        host_key,
        "/srv/source/",
        local,
        material,
        budget,
    )

    assert observed["executable"] == Path("/usr/bin/rsync")
    args = cast(tuple[str, ...], observed["args"])
    assert "-aHAX" in args
    assert args[-2:] == ("backupbot@backup.example:/srv/source/", str(local))
    assert "--secluded-args" in args
    remote_shell = args[args.index("-e") + 1]
    assert "StrictHostKeyChecking=yes" in remote_shell
    assert "UpdateHostKeys=no" in remote_shell
    assert "IdentitiesOnly=yes" in remote_shell
    assert "BatchMode=yes" in remote_shell
    assert "PasswordAuthentication=no" in remote_shell
    assert stat.S_IMODE((material / "id").stat().st_mode) == 0o600
    assert stat.S_IMODE((material / "known_hosts").stat().st_mode) == 0o600
    assert b"[backup.example]:2222 " in (material / "known_hosts").read_bytes()
    assert budget.bytes_used == len(b"remote data")
    assert budget.files_used == 1
    started = next(values for event, values in events if event == "remote_rsync_started")
    assert started["remote_source"] == "/srv/source/"
    assert started["local_destination"] == str(local)
    logged_arguments = cast(tuple[str, ...], started["arguments"])
    assert logged_arguments[-2] == "backup.example:/srv/source/"
    assert "backupbot@" not in repr(logged_arguments)
    assert str(material) not in repr(logged_arguments)
    completed = next(
        values for event, values in events if event == "remote_rsync_process_completed"
    )
    assert completed == {
        "return_code": 0,
        "stdout": "rsync stdout",
        "stderr": "rsync stderr",
    }


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_rsync_termination_reaps_owned_process_group(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    server = _remote_server(private_key)
    credentials = SSHCredentials(
        server.host, server.ssh_port, server.ssh_username, private_key, None
    )
    material = tmp_path / "ssh-material"
    material.mkdir(mode=0o700)
    descendant_pid_file = tmp_path / "ssh-child.pid"
    created: list[asyncio.subprocess.Process] = []
    real_create = asyncio.create_subprocess_exec

    async def capturing_create(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_create(*args, **kwargs)
        created.append(process)
        return process

    async def stalled_rsync(
        _executable: Path, _args: tuple[str, ...], **kwargs: object
    ) -> ProcessResult:
        timeout = 0.5 if failure == "timeout" else cast(float, kwargs["timeout"])
        program = (
            "import pathlib,subprocess,sys,time;"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid));"
            "time.sleep(60)"
        )
        return await supervised_process(
            Path(sys.executable),
            ("-c", program, str(descendant_pid_file)),
            timeout=timeout,
            capacity_check=cast(Any, kwargs.get("capacity_check")),
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capturing_create)
    monkeypatch.setattr(remote_files, "run_process", stalled_rsync)
    transfer = asyncio.create_task(
        remote_files._rsync_download(
            server,
            credentials,
            host_key,
            "/srv/source/",
            tmp_path / "remote-download" / "0",
            material,
            SourceBudget(settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}), tmp_path),
        )
    )
    for _ in range(100):
        if created and descendant_pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert created
    assert descendant_pid_file.exists()

    if failure == "cancel":
        transfer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transfer
    else:
        with pytest.raises(PipelineError) as caught:
            await transfer
        assert caught.value.code == "RSYNC_TIMEOUT"

    child = created[0]
    assert child.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)
    descendant_pid = int(descendant_pid_file.read_text())
    descendant_alive = True
    for _ in range(100):
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            descendant_alive = False
            break
        await asyncio.sleep(0.01)
    assert not descendant_alive


async def test_failed_rsync_transfer_cleans_run_workspace(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private_key = client_key.export_private_key("openssh").decode()
    cipher = SecretCipher(base64.b64encode(b"r" * 32).decode())
    server = _remote_server(private_key)
    server.encrypted_private_key = cipher.encrypt(
        private_key, context="remote-server-private-key-v1"
    )
    profile = BackupProfile(
        id=1,
        name="remote",
        source_type=SourceType.FILES_DIRECTORIES,
        files_source=FilesSource(
            profile_id=1,
            location=SourceLocation.REMOTE_SERVER,
            remote_server_id=1,
            remote_server=server,
            paths=[
                BackupPath(
                    id=1,
                    files_source_id=1,
                    path="/srv/source/",
                    sqlite_snapshot_candidate=False,
                )
            ],
        ),
    )

    class Connection:
        def get_server_host_key(self) -> asyncssh.SSHKey:
            return host_key

        async def run(self, _command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(exit_status=0, stdout="/usr/bin/rsync\n")

    class Context:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    async def failed_transfer(*args: object, **_kwargs: object) -> None:
        cast(Path, args[4]).parent.mkdir(parents=True, exist_ok=True)
        cast(Path, args[4]).write_bytes(b"partial")
        raise PipelineError("PROCESS_FAILED", "rsync transfer failed")

    monkeypatch.setattr(remote_files, "_ssh_gateway_factory", Gateway)
    monkeypatch.setattr(remote_files, "_rsync_download", failed_transfer)
    destination = tmp_path / "source.tar"

    with pytest.raises(PipelineError) as caught:
        await remote_files._package_remote(
            profile,
            destination,
            cipher,
            settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0}),
        )

    assert caught.value.code == "PROCESS_FAILED"
    assert not destination.exists()
    assert not (tmp_path / "remote-download").exists()
    assert not (tmp_path / "ssh-material").exists()
