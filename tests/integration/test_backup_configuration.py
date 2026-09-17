import base64

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from application.profile_config import (
    CompressionConfig,
    DatabaseConfig,
    EncryptionConfig,
    FilesConfig,
    ScheduleConfig,
)
from application.remote_server_service import RemoteServerService
from application.services import BackupService, BootstrapService
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    ScheduleKind,
    SourceLocation,
    SourceType,
    TLSMode,
)
from infrastructure.repositories import RemoteServerRepository
from infrastructure.ssh import SSHCredentials
from security import SecretCipher


class FakeSSHGateway:
    async def discover_fingerprint(self, credentials: SSHCredentials) -> str:
        assert credentials.private_key == "private-key"
        return "SHA256:trusted"

    async def test_connection(self, credentials: SSHCredentials, expected_fingerprint: str) -> None:
        assert credentials.private_key == "private-key"
        assert expected_fingerprint == "SHA256:trusted"


@pytest.mark.asyncio
async def test_remote_crud_and_configured_profiles(session: AsyncSession) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = SecretCipher(base64.b64encode(b"z" * 32).decode())
    remote_service = RemoteServerService(session, cipher, FakeSSHGateway())  # type: ignore[arg-type]
    pending = await remote_service.probe(
        actor,
        name="storage",
        host="backup.internal",
        port=22,
        username="backup",
        private_key="private-key",
        passphrase="key-secret",
    )
    server = await remote_service.confirm(actor, pending)
    assert server.authentication_method.value == "PRIVATE_KEY"
    assert b"private-key" not in server.encrypted_private_key
    await remote_service.test(actor, server.id)

    replacement = await remote_service.probe(
        actor,
        name="storage-renamed",
        host="backup-2.internal",
        port=2222,
        username="backup2",
        private_key="private-key",
    )
    server = await remote_service.update(actor, server.id, replacement)
    assert server.name == "storage-renamed"
    assert server.host == "backup-2.internal"
    assert server.ssh_port == 2222
    assert server.host_key_fingerprint == "SHA256:trusted"
    assert server.encrypted_key_passphrase is None

    backup_service = BackupService(
        session, cipher, allowed_local_roots=("/srv",), denied_local_roots=("/proc",)
    )
    database = await backup_service.create_database_profile(
        actor,
        name="postgres",
        description=None,
        database=DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "db.internal",
            5432,
            "app",
            "backup",
            "db-secret",
            TLSMode.REQUIRED,
        ),
        schedule=ScheduleConfig(ScheduleKind.DAILY, time="02:00"),
        compression=CompressionConfig(CompressionFormat.ZSTANDARD, CompressionPreset.BALANCED),
        encryption=EncryptionConfig(EncryptionMode.AGE_PASSPHRASE, passphrase="archive-secret"),
        admin_ids=set(),
    )
    assert database.enabled
    assert database.next_scheduled_at is not None
    assert database.database_source is not None
    assert b"db-secret" not in database.database_source.encrypted_password

    files = await backup_service.create_files_profile(
        actor,
        name="sqlite-files",
        description="snapshot aware",
        files=FilesConfig(SourceLocation.REMOTE_SERVER, ("/data/app.sqlite3",), server.id),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.NONE, None),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
    )
    assert files.files_source is not None
    assert files.files_source.paths[0].sqlite_snapshot_candidate

    await remote_service.set_enabled(actor, server.id, False)
    assert not (await RemoteServerRepository(session).active_by_id(server.id)).enabled
    await remote_service.delete(actor, server.id)
    assert await RemoteServerRepository(session).list_active() == []


@pytest.mark.asyncio
async def test_profile_edit_preserves_disabled_state(session: AsyncSession) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    profile = await BackupService(session).create_draft(
        actor, "disabled-profile", None, SourceType.FILES_DIRECTORIES
    )
    service = BackupService(session)

    await service.edit(actor, profile.id, enabled=False)
    updated = await service.edit(actor, profile.id, description="still disabled")

    assert not updated.enabled


@pytest.mark.asyncio
async def test_local_profile_persists_canonical_host_path_from_legacy_mount_path(
    session: AsyncSession,
) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    service = BackupService(
        session,
        allowed_local_roots=("/srv/backups",),
        denied_local_roots=("/proc",),
        local_source_mounts=(("/etc", "/host/etc"),),
    )
    profile = await service.create_files_profile(
        actor,
        name="local-nginx",
        description=None,
        files=FilesConfig(SourceLocation.LOCAL, ("/host/etc/nginx",)),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.NONE, None),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
    )

    assert profile.files_source is not None
    assert [item.path for item in profile.files_source.paths] == ["/etc/nginx"]
