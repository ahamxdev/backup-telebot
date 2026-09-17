# ruff: noqa: ASYNC240 -- tests intentionally exercise synchronous local filesystem cleanup

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from database.models import (
    BackupProfile,
    BackupRun,
    DatabaseSource,
    MSSQLArtifactCleanup,
    RemoteServer,
    User,
)
from domain.enums import (
    CompressionFormat,
    DatabaseEngine,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
    TLSMode,
)
from pipeline import acquisition as acquisition_module
from pipeline import worker as worker_module
from pipeline.acquisition import (
    _mssql_execution_path,
    cleanup_mssql_server_artifact,
)
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.transforms import sha256_file
from pipeline.worker import PipelineRunner, cleanup_orphaned_mssql_artifacts
from security import SecretCipher


def cipher() -> SecretCipher:
    return SecretCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


def source_for(base: Path, *, docker_path: str | None = None) -> DatabaseSource:
    return DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MSSQL,
        host="sql.internal",
        port=1433,
        database_name="production",
        username="backup-user",
        encrypted_password=b"unused",
        tls_mode=TLSMode.DISABLED,
        mssql_in_docker=docker_path is not None,
        mssql_backup_path=docker_path or str(base),
        mssql_host_backup_path=str(base) if docker_path is not None else None,
    )


async def persisted_cleanup(
    session,
    base: Path,
    operation_id: str,
    *,
    status: RunStatus,
    retrieved: bool,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> tuple[BackupRun, MSSQLArtifactCleanup, Path]:
    owner = User(telegram_user_id=7711, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name=f"mssql-{operation_id[:4]}",
        source_type=SourceType.DATABASE,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )
    profile.database_source = source_for(base)
    session.add(profile)
    await session.flush()
    run = BackupRun(
        uuid=f"00000000-0000-4000-8000-{operation_id[:12]}",
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=status,
        stage=RunStage.BACKUP if status is RunStatus.RUNNING else RunStage.COMPLETE,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    session.add(run)
    await session.flush()
    fenced = Path(_mssql_execution_path(str(base), operation_id))
    fenced.write_bytes(b"verified server backup")
    record = MSSQLArtifactCleanup(
        run_id=run.id,
        operation_id=operation_id,
        artifact_path=str(fenced),
        retrieved_at=datetime.now(UTC) if retrieved else None,
        size_bytes=fenced.stat().st_size if retrieved else None,
        sha256=sha256_file(fenced) if retrieved else None,
    )
    session.add(record)
    await session.commit()
    return run, record, fenced


async def test_non_docker_cleanup_deletes_only_exact_verified_fence(tmp_path: Path) -> None:
    base = tmp_path / "production.bak"
    operation_id = "1" * 32
    fenced = Path(_mssql_execution_path(str(base), operation_id))
    another = Path(_mssql_execution_path(str(base), "2" * 32))
    fenced.write_bytes(b"current run")
    another.write_bytes(b"another run")

    await cleanup_mssql_server_artifact(
        source_for(base),
        str(fenced),
        operation_id,
        fenced.stat().st_size,
        sha256_file(fenced),
        cipher(),
    )

    assert not fenced.exists()
    assert another.read_bytes() == b"another run"


async def test_docker_cleanup_uses_host_visible_mounted_path(tmp_path: Path) -> None:
    host_base = tmp_path / "mounted" / "production.bak"
    host_base.parent.mkdir()
    operation_id = "3" * 32
    fenced = Path(_mssql_execution_path(str(host_base), operation_id))
    fenced.write_bytes(b"container backup")
    source = source_for(host_base, docker_path="/var/opt/mssql/backup/production.bak")

    await cleanup_mssql_server_artifact(
        source,
        str(fenced),
        operation_id,
        fenced.stat().st_size,
        sha256_file(fenced),
        cipher(),
    )

    assert not fenced.exists()


async def test_remote_cleanup_uses_pinned_server_command_without_sftp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = "9" * 32
    base = Path("/srv/mssql/production.bak")
    artifact_path = _mssql_execution_path(str(base), operation_id)
    payload = b"remote verified backup"
    secret_cipher = cipher()
    server = RemoteServer(
        id=9,
        name="artifact-host",
        host="storage.internal",
        ssh_port=22,
        ssh_username="backup",
        encrypted_private_key=secret_cipher.encrypt(
            "private-key", context="remote-server-private-key-v1"
        ),
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
    )
    source = source_for(base)
    source.artifact_remote_server_id = server.id
    source.artifact_remote_server = server
    commands: list[str] = []
    observed_fingerprints: list[str] = []

    class Connection:
        async def run(self, command: str, **_kwargs: object) -> SimpleNamespace:
            commands.append(command)
            return SimpleNamespace(exit_status=0)

        def start_sftp_client(self) -> None:
            pytest.fail("SFTP must not be used for remote cleanup")

    class Gateway:
        @asynccontextmanager
        async def pinned_connection(
            self, _credentials: object, fingerprint: str, **_kwargs: object
        ) -> AsyncIterator[Connection]:
            observed_fingerprints.append(fingerprint)
            yield Connection()

    monkeypatch.setattr(acquisition_module, "SSHGateway", Gateway)

    await cleanup_mssql_server_artifact(
        source,
        artifact_path,
        operation_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        secret_cipher,
    )

    assert observed_fingerprints == ["SHA256:trusted"]
    assert len(commands) == 1
    assert artifact_path in commands[0]


async def test_wrong_or_another_run_path_is_refused(tmp_path: Path) -> None:
    base = tmp_path / "production.bak"
    current_id = "4" * 32
    other = Path(_mssql_execution_path(str(base), "5" * 32))
    other.write_bytes(b"another run")

    with pytest.raises(PipelineError) as caught:
        await cleanup_mssql_server_artifact(
            source_for(base),
            str(other),
            current_id,
            other.stat().st_size,
            sha256_file(other),
            cipher(),
        )

    assert caught.value.code == "MSSQL_CLEANUP_REFUSED"
    assert other.read_bytes() == b"another run"


async def test_cleanup_failure_preserves_local_backup_and_records_warning(
    database, session, settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "6" * 32
    run, record, fenced = await persisted_cleanup(
        session,
        tmp_path / "production.bak",
        operation_id,
        status=RunStatus.RUNNING,
        retrieved=True,
        lease_owner="worker-a",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    local_backup = tmp_path / "retrieved.bak"
    local_backup.write_bytes(fenced.read_bytes())
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        }
    )
    runner = PipelineRunner(database.session_factory, configured, object(), "worker-a")

    async def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise PipelineError("MSSQL_CLEANUP_FAILED", "safe cleanup failure")

    monkeypatch.setattr(worker_module, "cleanup_mssql_server_artifact", fail_cleanup)
    warning = await runner._cleanup_mssql_record(session, run, record, RunDeadline(60))

    assert warning == "MSSQL_CLEANUP_FAILED"
    assert local_backup.read_bytes() == b"verified server backup"
    assert fenced.exists()
    await session.refresh(record)
    assert record.cleaned_at is None
    assert record.last_error == "MSSQL_CLEANUP_FAILED"


async def test_cancelled_unretrieved_artifact_is_not_automatically_deleted(
    database, session, settings, tmp_path: Path
) -> None:
    _run, record, fenced = await persisted_cleanup(
        session,
        tmp_path / "production.bak",
        "7" * 32,
        status=RunStatus.CANCELLED,
        retrieved=False,
    )
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        }
    )

    await cleanup_orphaned_mssql_artifacts(database.session_factory, configured)

    assert fenced.exists()
    await session.refresh(record)
    assert record.cleaned_at is None


async def test_reclaimed_cleanup_waits_for_lease_then_is_restart_safe(
    database, session, settings, tmp_path: Path
) -> None:
    run, record, fenced = await persisted_cleanup(
        session,
        tmp_path / "production.bak",
        "8" * 32,
        status=RunStatus.RUNNING,
        retrieved=True,
        lease_owner="old-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        }
    )

    await cleanup_orphaned_mssql_artifacts(database.session_factory, configured)
    assert fenced.exists()

    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    await cleanup_orphaned_mssql_artifacts(database.session_factory, configured)
    await session.refresh(record)
    assert not fenced.exists()
    assert record.cleaned_at is not None
    assert record.last_error is None

    await cleanup_orphaned_mssql_artifacts(database.session_factory, configured)
    await session.refresh(record)
    assert record.cleaned_at is not None
