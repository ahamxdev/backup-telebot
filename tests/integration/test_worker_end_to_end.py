import os
import tarfile
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
    FilesSource,
    GitLabSource,
    User,
)
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStatus,
    ScheduleKind,
    SourceLocation,
    SourceType,
)
from pipeline.acquisition import gitlab as gitlab_provider
from pipeline.jobs import RunService
from pipeline.worker import PipelineRunner
from security import SecretCipher


class SuccessfulTransport:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def send_file(self, entity: int, file: str, **kwargs: object) -> object:
        del entity, kwargs
        self.paths.append(file)
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
    key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr(key),
            "ALLOWED_SOURCE_ROOTS": str(root),
            "GITLAB_HELPER_SOCKET": tmp_path / "control" / "helper.sock",
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

    async def invoke(source: GitLabSource, configured_settings, deadline_seconds: float) -> None:
        del source, configured_settings, deadline_seconds
        payload = tmp_path / "gitlab-payload"
        payload.write_bytes(os.urandom(4096))
        with tarfile.open(root / "200_gitlab_backup.tar", "w") as native:
            native.add(payload, arcname="repositories/data")

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
    assert len(transport.paths) > 2
    assert list(root.iterdir()) == []
