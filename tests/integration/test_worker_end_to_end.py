import hashlib
import io
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr
from sqlalchemy import select

from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    GitLabConfig,
    ScheduleConfig,
)
from application.services import BackupService
from database.models import (
    AdminBackupAccess,
    AuditEvent,
    BackupPath,
    BackupProfile,
    BackupRun,
    FilesSource,
    GitLabSource,
    RemoteServer,
    User,
)
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceLocation,
    SourceType,
    SSHAuthenticationMethod,
)
from pipeline import worker as worker_provider
from pipeline.acquisition import gitlab as gitlab_provider
from pipeline.deadline import RunDeadline
from pipeline.finalize import finalize_artifact
from pipeline.jobs import RunService
from pipeline.worker import PipelineRunner
from security import SecretCipher


class SuccessfulTransport:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.captions: list[str] = []

    async def send_file(self, entity: int, file: str, **kwargs: object) -> object:
        del entity
        self.paths.append(file)
        self.captions.append(str(kwargs["caption"]))
        return SimpleNamespace(id=7, media=SimpleNamespace(document=SimpleNamespace(id=8)))


async def test_local_worker_pipeline_to_delivery_and_cleanup(
    database, session, settings, tmp_path: Path
) -> None:
    source_root = tmp_path / "sources"
    source_root.joinpath("nginx").mkdir(parents=True)
    source_file = source_root / "nginx" / "payload.txt"
    source_file.write_text("production backup payload")
    host_root = "/etc"
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "ALLOWED_SOURCE_ROOTS": "/srv/backups",
            "LOCAL_SOURCE_MOUNTS": f"{host_root}:{source_root}",
            "MIN_FREE_DISK_BYTES": 0,
        }
    )
    owner = User(telegram_user_id=101, display_name="root", role=Role.SUPERADMIN)
    admin = User(telegram_user_id=102, display_name="admin", role=Role.ADMIN)
    session.add_all((owner, admin))
    await session.flush()
    profile = BackupProfile(
        name="Local E2E",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path="/etc/nginx/payload.txt", sqlite_snapshot_candidate=False)],
        ),
    )
    session.add(profile)
    await session.flush()
    session.add(
        AdminBackupAccess(admin_id=admin.id, backup_profile_id=profile.id, created_by=owner.id)
    )
    await session.commit()
    queued = await RunService(session).enqueue_manual(owner, profile.id)
    claimed = await RunService(session).claim("worker", 120, (RunStatus.QUEUED,))
    assert claimed and claimed.id == queued.run.id
    transport = SuccessfulTransport()

    await PipelineRunner(database.session_factory, configured, transport, "worker").execute(
        claimed.id
    )

    await session.refresh(queued.run)
    assert queued.run.status is RunStatus.SUCCEEDED, (
        queued.run.error_code,
        queued.run.error_message,
    )
    assert queued.run.pending_directory is None
    assert queued.run.original_size_bytes == source_file.stat().st_size
    assert queued.run.final_size_bytes is not None
    assert all("Original Backup Size:</b> 25 B" in item for item in transport.captions)
    assert len(transport.paths) == 2
    assert not configured.WORK_ROOT.joinpath(queued.run.uuid).exists()
    transitions = set(
        await session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.target_type == "backup_run",
                AuditEvent.target_id == str(queued.run.id),
            )
        )
    )
    assert {
        "backup_run_queued",
        "backup_run_started",
        "backup_run_delivery_pending",
        "backup_run_succeeded",
    } <= transitions


async def test_gitlab_uses_archive_compression_encryption_and_multipart_pipeline(
    database, session, settings, tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "gitlab-native"
    root.mkdir()
    staging = tmp_path / "gitlab-staging"
    staging.mkdir()
    key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr(key),
            "ALLOWED_SOURCE_ROOTS": str(root),
            "GITLAB_HELPER_SOCKET": tmp_path / "control" / "helper.sock",
            "GITLAB_LOCAL_BACKUP_PATH": str(root),
            "GITLAB_STAGING_ROOT": staging,
            "GITLAB_STABILITY_INTERVAL_SECONDS": 0.001,
            "GITLAB_STABILITY_CHECKS": 2,
            "TELEGRAM_MAX_FILE_BYTES": 128,
            "MIN_FREE_DISK_BYTES": 0,
        }
    )
    owner = User(telegram_user_id=201, display_name="root", role=Role.SUPERADMIN)
    admin = User(telegram_user_id=202, display_name="admin", role=Role.ADMIN)
    session.add_all((owner, admin))
    await session.flush()
    service = BackupService(
        session,
        SecretCipher(key),
        allowed_local_roots=configured.allowed_source_roots(),
        denied_local_roots=configured.denied_source_roots(),
        local_source_mounts=configured.local_source_mounts(),
        local_gitlab_backup_path=configured.GITLAB_LOCAL_BACKUP_PATH,
    )
    profile = await service.create_gitlab_profile(
        owner,
        name="GitLab E2E",
        description=None,
        gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab", str(root)),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.GZIP, CompressionPreset.BALANCED),
        encryption=EncryptionConfig(EncryptionMode.AGE_PASSPHRASE, passphrase="strong secret"),
        admin_ids={admin.id},
    )

    native_sizes: list[int] = []

    async def invoke(
        source: GitLabSource, configured_settings, deadline_seconds: float
    ) -> dict[str, object]:
        del source, configured_settings, deadline_seconds
        payload = tmp_path / "gitlab-payload"
        payload.write_bytes(os.urandom(4096))
        staging_name = "c" * 32 + ".gitlab.tar"
        staged = staging / staging_name
        with tarfile.open(staged, "w") as native:
            native.add(payload, arcname="repositories/data")
        native_sizes.append(staged.stat().st_size)
        return {
            "ok": True,
            "staging_name": staging_name,
            "native_name": "200_gitlab_backup.tar",
            "size": staged.stat().st_size,
            "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        }

    monkeypatch.setattr(gitlab_provider, "_invoke_helper", invoke)
    queued = await RunService(session).enqueue_manual(owner, profile.id)
    claimed = await RunService(session).claim("gitlab-worker", 120, (RunStatus.QUEUED,))
    assert claimed is not None
    transport = SuccessfulTransport()

    await PipelineRunner(database.session_factory, configured, transport, "gitlab-worker").execute(
        queued.run.id
    )

    await session.refresh(queued.run)
    assert queued.run.status is RunStatus.SUCCEEDED, queued.run.error_message
    assert queued.run.final_filename is not None
    assert queued.run.final_filename.endswith(".tar.gz.age")
    assert queued.run.original_size_bytes == native_sizes[0]
    assert queued.run.final_size_bytes is not None
    assert queued.run.final_size_bytes != queued.run.original_size_bytes
    assert len(transport.paths) > 2
    expected_original = f"{native_sizes[0] / 1024:.2f} KiB"
    assert all(expected_original in caption for caption in transport.captions)
    assert all("Final Backup Size:" in caption for caption in transport.captions)
    assert list(root.iterdir()) == []


async def test_remote_gitlab_persists_verified_native_size_before_transforms(
    database, session, settings, tmp_path: Path, monkeypatch
) -> None:
    key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr(key),
            "MIN_FREE_DISK_BYTES": 0,
        }
    )
    owner = User(telegram_user_id=211, display_name="root", role=Role.SUPERADMIN)
    server = RemoteServer(
        name="gitlab-host",
        host="gitlab.example",
        ssh_port=22,
        ssh_username="backup",
        authentication_method=SSHAuthenticationMethod.PRIVATE_KEY,
        encrypted_private_key=b"encrypted-key",
        encrypted_key_passphrase=None,
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
    )
    session.add_all((owner, server))
    await session.flush()
    profile = await BackupService(session, SecretCipher(key)).create_gitlab_profile(
        owner,
        name="Remote GitLab E2E",
        description=None,
        gitlab=GitLabConfig(
            SourceLocation.REMOTE_SERVER,
            "gitlab",
            "/opt/gitlab/data/backups",
            server.id,
        ),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.GZIP, CompressionPreset.BALANCED),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
    )
    native_stream = io.BytesIO()
    native_content = b"native-gitlab-data" * 4096
    with tarfile.open(fileobj=native_stream, mode="w") as native:
        entry = tarfile.TarInfo("repositories/data")
        entry.size = len(native_content)
        native.addfile(entry, io.BytesIO(native_content))
    payload = native_stream.getvalue()
    native_size = len(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    state = gitlab_provider._FileState("300_gitlab_backup.tar", 30, native_size, 456)
    snapshots = iter(({}, {state.name: state}))

    class Connection:
        def get_server_host_key(self) -> object:
            return object()

    class Context:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Gateway:
        def pinned_connection(self, *_args: object, **_kwargs: object) -> Context:
            return Context()

    async def snapshot(*_args: object) -> dict[str, gitlab_provider._FileState]:
        return next(snapshots)

    async def stable(*_args: object) -> gitlab_provider._FileState:
        return state

    async def invoke(*_args: object) -> None:
        return None

    async def operation(
        _connection: object,
        operation_name: str,
        _root: str,
        _state: gitlab_provider._FileState | None,
        _deadline: object,
    ) -> str:
        return checksum if operation_name == "hash" else ""

    async def rsync(*args: object, **_kwargs: object) -> tuple[int, int]:
        target = args[4]
        assert isinstance(target, Path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return native_size, 1

    monkeypatch.setattr(gitlab_provider, "SSHGateway", lambda: Gateway())
    monkeypatch.setattr(gitlab_provider, "_ssh_credentials", lambda *_args: object())
    monkeypatch.setattr(gitlab_provider, "_remote_snapshot", snapshot)
    monkeypatch.setattr(gitlab_provider, "_invoke_remote_backup", invoke)
    monkeypatch.setattr(gitlab_provider, "_remote_stable_file", stable)
    monkeypatch.setattr(gitlab_provider, "_remote_file_operation", operation)
    monkeypatch.setattr(gitlab_provider, "_rsync_download", rsync)

    original_create_archive = worker_provider.create_archive
    persisted_before_transform: list[int | None] = []

    async def assert_persisted_before_transform(*args: object, **kwargs: object) -> None:
        async with database.session_factory() as verification_session:
            stored = await verification_session.get(BackupRun, queued.run.id)
            assert stored is not None
            persisted_before_transform.append(stored.original_size_bytes)
        await original_create_archive(*args, **kwargs)

    monkeypatch.setattr(worker_provider, "create_archive", assert_persisted_before_transform)
    queued = await RunService(session).enqueue_manual(owner, profile.id)
    claimed = await RunService(session).claim("remote-gitlab-worker", 120, (RunStatus.QUEUED,))
    assert claimed is not None
    transport = SuccessfulTransport()

    await PipelineRunner(
        database.session_factory, configured, transport, "remote-gitlab-worker"
    ).execute(queued.run.id)

    await session.refresh(queued.run)
    assert persisted_before_transform == [native_size]
    assert queued.run.status is RunStatus.SUCCEEDED, queued.run.error_message
    assert queued.run.original_size_bytes == native_size
    assert queued.run.final_size_bytes is not None
    assert queued.run.final_size_bytes < native_size
    expected_original = f"{native_size / 1024:.2f} KiB"
    assert all(expected_original in caption for caption in transport.captions)
    assert all("Final Backup Size:" in caption for caption in transport.captions)


async def test_finalized_recovery_restores_original_size_from_manifest(
    database, session, settings, tmp_path: Path
) -> None:
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "MIN_FREE_DISK_BYTES": 0,
        }
    )
    owner = User(telegram_user_id=301, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name="Recovery",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )
    session.add(profile)
    await session.flush()
    run = BackupRun(
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        stage=RunStage.FINALIZE,
        lease_owner="recovery-worker",
    )
    session.add(run)
    await session.flush()
    source = tmp_path / "recovered-artifact.tar"
    source.write_bytes(b"final artifact")
    finalized = finalize_artifact(
        source,
        configured.PENDING_DELIVERY_ROOT,
        profile.name,
        run.uuid,
        datetime.now(UTC),
        "UTC",
        source.name,
        {"kind": "recovery-test"},
        original_size_bytes=8192,
    )
    run.pending_directory = str(finalized.directory)
    await session.commit()
    runner = PipelineRunner(
        database.session_factory,
        configured,
        SuccessfulTransport(),
        "recovery-worker",
    )

    assert await runner._resume_finalized(  # noqa: SLF001 - recovery boundary regression
        session, run, RunDeadline(60)
    )

    assert run.original_size_bytes == 8192
    assert run.final_size_bytes == len(b"final artifact")
    assert run.status is RunStatus.DELIVERY_PENDING
