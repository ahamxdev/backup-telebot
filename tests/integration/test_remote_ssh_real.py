from __future__ import annotations

import asyncio
import base64
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import pytest

from database.models import BackupPath, BackupProfile, FilesSource, RemoteServer
from domain.enums import SourceLocation, SourceType, SSHAuthenticationMethod
from infrastructure.ssh import HostKeyChangedError, SSHCredentials, SSHGateway
from pipeline import acquisition
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from security import SecretCipher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_CONTEXT = PROJECT_ROOT / "tests/integration/ssh_server"
DOCKER_EXECUTABLE = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - arguments are controlled by this integration test
        [str(DOCKER_EXECUTABLE), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = result.stderr or result.stdout
        raise AssertionError(f"Docker {args[0]} failed: {detail[-4000:].strip()}")
    return result


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    result = docker("info", check=False, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        pytest.skip(f"Docker daemon is unavailable: {detail[-1] if detail else 'unknown error'}")


def wait_for_ssh(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
                connection.settimeout(1)
                if connection.recv(255).startswith(b"SSH-"):
                    return
        except OSError:
            time.sleep(0.25)
    raise AssertionError("disposable SSH server did not become ready")


@dataclass(frozen=True)
class SSHTestServer:
    port: int
    private_key: str
    fingerprint: str
    container: str


@pytest.fixture
def real_ssh_server(tmp_path: Path) -> Iterator[SSHTestServer]:
    require_docker()
    suffix = uuid.uuid4().hex[:12]
    image = f"backup-telebot-ssh-test:{suffix}"
    container = f"backup-telebot-ssh-test-{suffix}"
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_private = client_key.export_private_key("openssh").decode()
    (keys / "client.pub").write_bytes(client_key.export_public_key("openssh"))
    (keys / "host_key").write_bytes(host_key.export_private_key("openssh"))
    (keys / "host_key.pub").write_bytes(host_key.export_public_key("openssh"))
    for path in keys.iterdir():
        path.chmod(0o600)
    image_created = container_created = False
    try:
        docker("build", "--tag", image, str(SERVER_CONTEXT), timeout=1200)
        image_created = True
        docker(
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            "127.0.0.1::22",
            "--mount",
            f"type=bind,src={keys},dst=/run/test-keys,readonly",
            image,
        )
        container_created = True
        port_result = docker("port", container, "22/tcp", timeout=30)
        port = int(port_result.stdout.strip().rsplit(":", 1)[1])
        wait_for_ssh(port)
        yield SSHTestServer(port, client_private, host_key.get_fingerprint("sha256"), container)
    finally:
        if container_created:
            docker("rm", "--force", container, check=False, timeout=30)
        if image_created:
            docker("image", "rm", "--force", image, check=False, timeout=60)


def profile_for(
    test_server: SSHTestServer, cipher: SecretCipher, fingerprint: str
) -> BackupProfile:
    server = RemoteServer(
        id=1,
        name="disposable-ssh",
        host="127.0.0.1",
        ssh_port=test_server.port,
        ssh_username="backupbot",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=cipher.encrypt(
            test_server.private_key, context="remote-server-private-key-v1"
        ),
        encrypted_key_passphrase=None,
        host_key_fingerprint=fingerprint,
        enabled=True,
        deleted_at=None,
    )
    files = FilesSource(
        profile_id=1,
        location=SourceLocation.REMOTE_SERVER,
        remote_server_id=1,
        remote_server=server,
        paths=[
            BackupPath(
                id=1,
                files_source_id=1,
                path="/srv/backup/source/",
                sqlite_snapshot_candidate=False,
            )
        ],
    )
    return BackupProfile(
        id=1,
        name="remote-integration",
        source_type=SourceType.FILES_DIRECTORIES,
        files_source=files,
    )


def assert_archive_content(archive_path: Path) -> None:
    with tarfile.open(archive_path, "r") as archive:
        names = archive.getnames()
        assert any(name.endswith("root.txt") for name in names)
        assert any(name.endswith("nested/data.txt") for name in names)


def assert_temporary_material_removed(run_root: Path) -> None:
    assert not (run_root / "ssh-material").exists()
    assert not (run_root / "remote-download").exists()


async def test_real_ssh_pinning_and_rsync_acquisition(
    real_ssh_server: SSHTestServer,
    tmp_path: Path,
    settings,
) -> None:
    credentials = SSHCredentials(
        "127.0.0.1",
        real_ssh_server.port,
        "backupbot",
        real_ssh_server.private_key,
        None,
    )
    gateway = SSHGateway()
    async with gateway.pinned_connection(credentials, real_ssh_server.fingerprint) as connection:
        assert not connection.is_closed()
        version = await connection.run("rsync --version", check=True)
        assert "rsync" in version.stdout.casefold()
    assert connection.is_closed()
    with pytest.raises(HostKeyChangedError):
        await gateway.test_connection(credentials, "SHA256:wrong-fingerprint")

    cipher = SecretCipher(base64.b64encode(b"r" * 32).decode())
    profile = profile_for(real_ssh_server, cipher, real_ssh_server.fingerprint)
    resource_settings = settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0})
    rsync_root = tmp_path / "rsync-run"
    rsync_root.mkdir(mode=0o700)
    rsync_archive = rsync_root / "source.tar"

    await acquisition._package_remote(profile, rsync_archive, cipher, resource_settings)
    assert_archive_content(rsync_archive)
    assert_temporary_material_removed(rsync_root)

    sqlite_profile = profile_for(real_ssh_server, cipher, real_ssh_server.fingerprint)
    assert sqlite_profile.files_source is not None
    sqlite_profile.files_source.paths = [
        BackupPath(
            id=2,
            files_source_id=1,
            path="/srv/backup/source/state.sqlite3",
            sqlite_snapshot_candidate=True,
        )
    ]
    sqlite_root = tmp_path / "sqlite-run"
    sqlite_root.mkdir(mode=0o700)
    sqlite_archive = sqlite_root / "source.tar"
    await acquisition._package_remote(sqlite_profile, sqlite_archive, cipher, resource_settings)
    extracted = sqlite_root / "extracted"
    extracted.mkdir()
    with tarfile.open(sqlite_archive, "r") as archive:
        archive.extractall(extracted, filter="data")
    snapshots = list(extracted.rglob("*.sqlite")) + list(extracted.rglob("[0-9]"))
    snapshot = next(path for path in snapshots if path.is_file())
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("remote-snapshot",)
    assert_temporary_material_removed(sqlite_root)
    async with gateway.pinned_connection(credentials, real_ssh_server.fingerprint) as connection:
        result = await connection.run(
            "find /tmp -maxdepth 1 -name 'backup-telebot-*.sqlite' -print", check=True
        )
        assert not result.stdout.strip()

    interrupted_root = tmp_path / "network-interrupted-run"
    interrupted_root.mkdir(mode=0o700)
    await asyncio.to_thread(docker, "pause", real_ssh_server.container)
    try:
        with pytest.raises(PipelineError) as interrupted:
            await acquisition._package_remote(
                profile,
                interrupted_root / "source.tar",
                cipher,
                resource_settings,
                RunDeadline(0.5),
            )
        assert interrupted.value.code in {"BACKUP_TIMEOUT", "SSH_CONNECT_TIMEOUT"}
    finally:
        await asyncio.to_thread(docker, "unpause", real_ssh_server.container)
    assert_temporary_material_removed(interrupted_root)

    failed_root = tmp_path / "failed-run"
    failed_root.mkdir(mode=0o700)
    wrong_profile = profile_for(real_ssh_server, cipher, "SHA256:wrong-fingerprint")
    with pytest.raises(PipelineError) as host_key_failure:
        await acquisition._package_remote(
            wrong_profile, failed_root / "source.tar", cipher, resource_settings
        )
    assert host_key_failure.value.code == "REMOTE_HOST_KEY_MISMATCH"
    assert_temporary_material_removed(failed_root)

    auth_failed_root = tmp_path / "auth-failed-run"
    auth_failed_root.mkdir(mode=0o700)
    unauthorized_key = asyncssh.generate_private_key("ssh-ed25519")
    unauthorized_server = SSHTestServer(
        real_ssh_server.port,
        unauthorized_key.export_private_key("openssh").decode(),
        real_ssh_server.fingerprint,
        real_ssh_server.container,
    )
    unauthorized_profile = profile_for(unauthorized_server, cipher, real_ssh_server.fingerprint)
    with pytest.raises(PipelineError) as authentication_failure:
        await acquisition._package_remote(
            unauthorized_profile,
            auth_failed_root / "source.tar",
            cipher,
            resource_settings,
        )
    assert authentication_failure.value.code == "REMOTE_AUTHENTICATION_FAILED"
    assert_temporary_material_removed(auth_failed_root)
