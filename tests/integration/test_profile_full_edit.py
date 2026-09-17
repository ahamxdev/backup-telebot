import base64
import json
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from application.profile_config import (
    CompressionConfig,
    DatabaseConfig,
    EncryptionConfig,
    FilesConfig,
    ScheduleConfig,
)
from application.run_config import restore_run_config, snapshot_legacy_active_runs
from application.services import AdminService, BackupService, BootstrapService
from config.settings import Settings
from database.models import AuditEvent, BackupRun, RemoteServer, User
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    LanguageCode,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceLocation,
    SourceType,
    TLSMode,
)
from infrastructure.repositories import AccessRepository
from infrastructure.telegram.transport import TelegramTransport
from pipeline.jobs import RunService
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.i18n import t
from presentation.telegram.runtime.fsm import State
from security import SecretCipher


def _cipher() -> SecretCipher:
    return SecretCipher(base64.b64encode(b"e" * 32).decode())


def _service(session: Any, cipher: SecretCipher) -> BackupService:
    return BackupService(
        session,
        cipher,
        allowed_local_roots=("/srv",),
        denied_local_roots=("/proc", "/sys", "/dev", "/run"),
    )


async def _database_profile(session: Any, actor: Any, cipher: SecretCipher) -> Any:
    return await _service(session, cipher).create_database_profile(
        actor,
        name="database-old",
        description="old description",
        database=DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "old-db.internal",
            5432,
            "old_database",
            "old_user",
            "old-password",
            TLSMode.REQUIRED,
        ),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.GZIP, CompressionPreset.FAST),
        encryption=EncryptionConfig(EncryptionMode.AGE_PASSPHRASE, passphrase="old-age-secret"),
        admin_ids=set(),
    )


def _remote_server(name: str = "artifact-host") -> RemoteServer:
    return RemoteServer(
        name=name,
        host="ssh.internal",
        ssh_port=22,
        ssh_username="backup",
        encrypted_private_key=b"encrypted-private-key",
        host_key_fingerprint="SHA256:trusted",
        enabled=True,
    )


async def test_edit_database_schedule_compression_recipients_assignments_and_enabled(
    session,
) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    first_admin = await AdminService(session).create_admin(actor, 2002, "First", set())
    second_admin = await AdminService(session).create_admin(actor, 2003, "Second", set())
    cipher = _cipher()
    profile = await _database_profile(session, actor, cipher)
    original_password = profile.database_source.encrypted_password

    updated = await _service(session, cipher).update_configuration(
        actor,
        profile.id,
        name="database-new",
        description="new description",
        source_type=SourceType.DATABASE,
        database=DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "new-db.internal",
            5544,
            "new_database",
            "new_user",
            None,
            TLSMode.VERIFY_FULL,
            "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----",
        ),
        files=None,
        schedule=ScheduleConfig(ScheduleKind.WEEKLY, time="03:15", weekday=2),
        compression=CompressionConfig(CompressionFormat.ZSTANDARD, CompressionPreset.HIGH),
        encryption=EncryptionConfig(
            EncryptionMode.AGE_PUBLIC_KEY,
            recipients=("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",),
        ),
        admin_ids={second_admin.id},
        enabled=True,
        preserve_database_password=True,
    )

    assert updated.name == "database-new"
    assert updated.description == "new description"
    assert updated.enabled
    assert updated.schedule_kind is ScheduleKind.WEEKLY
    assert updated.schedule_time == "03:15" and updated.schedule_weekday == 2
    assert updated.compression_format is CompressionFormat.ZSTANDARD
    assert updated.compression_preset is CompressionPreset.HIGH
    assert updated.encryption_mode is EncryptionMode.AGE_PUBLIC_KEY
    assert updated.encrypted_age_passphrase is None
    assert json.loads(updated.age_recipients or "[]") == [
        "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    ]
    assert updated.database_source is not None
    assert updated.database_source.host == "new-db.internal"
    assert updated.database_source.port == 5544
    assert updated.database_source.database_name == "new_database"
    assert updated.database_source.username == "new_user"
    assert updated.database_source.tls_mode is TLSMode.VERIFY_FULL
    assert updated.database_source.encrypted_password == original_password
    assert await AccessRepository(session).admin_ids(profile.id) == {second_admin.id}
    assert first_admin.id not in await AccessRepository(session).admin_ids(profile.id)
    audit = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "backup_profile_configuration_updated")
        .order_by(AuditEvent.id.desc())
    )
    assert audit is not None
    assert "old-password" not in (audit.details or "")
    assert "old-age-secret" not in (audit.details or "")


async def test_database_password_replacement_preserves_age_secret(session) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = _cipher()
    profile = await _database_profile(session, actor, cipher)
    old_password = profile.database_source.encrypted_password
    old_age = profile.encrypted_age_passphrase

    updated = await _service(session, cipher).update_configuration(
        actor,
        profile.id,
        name=profile.name,
        description=profile.description,
        source_type=SourceType.DATABASE,
        database=DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "old-db.internal",
            5432,
            "old_database",
            "old_user",
            "replacement-password",
            TLSMode.REQUIRED,
        ),
        files=None,
        schedule=ScheduleConfig(ScheduleKind.DAILY, time="01:00"),
        compression=CompressionConfig(CompressionFormat.XZ, CompressionPreset.BALANCED),
        encryption=EncryptionConfig(EncryptionMode.AGE_PASSPHRASE),
        admin_ids=set(),
        enabled=False,
        preserve_age_passphrase=True,
    )

    assert updated.database_source is not None
    assert updated.database_source.encrypted_password != old_password
    assert (
        cipher.decrypt(
            updated.database_source.encrypted_password,
            context="database-password-v1",
        )
        == "replacement-password"
    )
    assert updated.encrypted_age_passphrase == old_age


async def test_edit_to_mssql_docker_paths_and_remote_server(session) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = _cipher()
    profile = await _database_profile(session, actor, cipher)
    remote = _remote_server()
    session.add(remote)
    await session.commit()

    updated = await _service(session, cipher).update_configuration(
        actor,
        profile.id,
        name=profile.name,
        description=None,
        source_type=SourceType.DATABASE,
        database=DatabaseConfig(
            DatabaseEngine.MSSQL,
            "sql.internal",
            1433,
            "application",
            "backup_user",
            "mssql-password",
            TLSMode.REQUIRED,
            mssql_in_docker=True,
            mssql_backup_path="/var/opt/mssql/backup/run.bak",
            mssql_host_backup_path="/srv/mssql/run.bak",
            artifact_remote_server_id=remote.id,
        ),
        files=None,
        schedule=ScheduleConfig(ScheduleKind.EVERY_N_HOURS, interval=6),
        compression=CompressionConfig(CompressionFormat.NONE, None),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
        enabled=True,
    )

    source = updated.database_source
    assert source is not None and source.engine is DatabaseEngine.MSSQL
    assert source.mssql_in_docker
    assert source.mssql_backup_path == "/var/opt/mssql/backup/run.bak"
    assert source.mssql_host_backup_path == "/srv/mssql/run.bak"
    assert source.artifact_remote_server_id == remote.id


async def test_edit_file_source_local_to_remote_and_validate_paths_atomically(session) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = _cipher()
    service = _service(session, cipher)
    profile = await service.create_files_profile(
        actor,
        name="files",
        description=None,
        files=FilesConfig(SourceLocation.LOCAL, ("/srv/old",)),
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.NONE, None),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
    )
    remote = _remote_server()
    session.add(remote)
    await session.commit()

    updated = await service.update_configuration(
        actor,
        profile.id,
        name="files",
        description=None,
        source_type=SourceType.FILES_DIRECTORIES,
        database=None,
        files=FilesConfig(SourceLocation.REMOTE_SERVER, ("/data/a", "/data/b"), remote.id),
        schedule=ScheduleConfig(ScheduleKind.MONTHLY, time="04:30", monthday=5),
        compression=CompressionConfig(CompressionFormat.ZIP, CompressionPreset.MAXIMUM),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
        enabled=True,
    )
    assert updated.files_source is not None
    assert updated.files_source.location is SourceLocation.REMOTE_SERVER
    assert updated.files_source.remote_server_id == remote.id
    assert [path.path for path in updated.files_source.paths] == ["/data/a", "/data/b"]

    with pytest.raises(ValueError):
        await service.update_configuration(
            actor,
            profile.id,
            name="partially-changed-name",
            description=None,
            source_type=SourceType.FILES_DIRECTORIES,
            database=None,
            files=FilesConfig(SourceLocation.LOCAL, ("/etc/forbidden",)),
            schedule=ScheduleConfig(ScheduleKind.MANUAL),
            compression=CompressionConfig(CompressionFormat.NONE, None),
            encryption=EncryptionConfig(EncryptionMode.NONE),
            admin_ids=set(),
            enabled=False,
        )
    await session.refresh(profile)
    assert profile.name == "files"
    assert profile.enabled
    await session.refresh(profile, ("files_source",))
    assert profile.files_source is not None
    assert profile.files_source.location is SourceLocation.REMOTE_SERVER


async def test_edit_requires_superadmin_and_allows_active_profile(session) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = _cipher()
    profile = await _database_profile(session, actor, cipher)
    normal_admin = await AdminService(session).create_admin(actor, 2002, "Admin", set())
    service = _service(session, cipher)
    kwargs = {
        "name": profile.name,
        "description": None,
        "source_type": SourceType.DATABASE,
        "database": DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "db.internal",
            5432,
            "app",
            "backup",
            None,
            TLSMode.REQUIRED,
        ),
        "files": None,
        "schedule": ScheduleConfig(ScheduleKind.MANUAL),
        "compression": CompressionConfig(CompressionFormat.NONE, None),
        "encryption": EncryptionConfig(EncryptionMode.NONE),
        "admin_ids": set(),
        "enabled": True,
        "preserve_database_password": True,
    }
    with pytest.raises(PermissionError):
        await service.update_configuration(normal_admin, profile.id, **kwargs)

    run = BackupRun(
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        stage=RunStage.BACKUP,
    )
    session.add(run)
    await session.commit()
    assert await snapshot_legacy_active_runs(session) == 1
    assert run.configuration_snapshot is not None
    updated = await service.update_configuration(actor, profile.id, **kwargs)
    assert updated.database_source is not None
    assert updated.database_source.host == "db.internal"
    legacy_config = restore_run_config(run.configuration_snapshot)
    assert legacy_config.database_source is not None
    assert legacy_config.database_source.host == "old-db.internal"


async def test_run_configuration_is_immutable_and_next_run_gets_edit(session) -> None:
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    cipher = _cipher()
    profile = await _database_profile(session, actor, cipher)
    first = await RunService(session).enqueue_manual(actor, profile.id)
    assert first.created and first.run.configuration_snapshot is not None
    assert "old-password" not in first.run.configuration_snapshot
    assert "old-age-secret" not in first.run.configuration_snapshot

    await _service(session, cipher).update_configuration(
        actor,
        profile.id,
        name="database-new",
        description="future runs only",
        source_type=SourceType.DATABASE,
        database=DatabaseConfig(
            DatabaseEngine.POSTGRESQL,
            "new-db.internal",
            6432,
            "new_database",
            "new_user",
            "new-password",
            TLSMode.REQUIRED,
        ),
        files=None,
        schedule=ScheduleConfig(ScheduleKind.MANUAL),
        compression=CompressionConfig(CompressionFormat.NONE, None),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
        enabled=True,
    )

    running_config = restore_run_config(first.run.configuration_snapshot)
    assert running_config.name == "database-old"
    assert running_config.database_source is not None
    assert running_config.database_source.host == "old-db.internal"
    assert (
        cipher.decrypt(
            running_config.database_source.encrypted_password,
            context="database-password-v1",
        )
        == "old-password"
    )

    first.run.status = RunStatus.SUCCEEDED
    first.run.stage = RunStage.COMPLETE
    await session.commit()
    second = await RunService(session).enqueue_manual(actor, profile.id)
    assert second.created and second.run.configuration_snapshot is not None
    next_config = restore_run_config(second.run.configuration_snapshot)
    assert next_config.name == "database-new"
    assert next_config.database_source is not None
    assert next_config.database_source.host == "new-db.internal"


class _FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)

    async def send_file(self, _entity: int, _file: str, **_kwargs: Any) -> None:
        raise AssertionError("not expected")


async def test_reconfigure_ui_is_bilingual_and_never_displays_existing_secrets(
    database, settings: Settings
) -> None:
    cipher = _cipher()
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await _database_profile(session, actor, cipher)
        actor.language_code = LanguageCode.PERSIAN
        await session.commit()
        actor_id = actor.id
        profile_id = profile.id
    configured = settings.model_copy(
        update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(base64.b64encode(b"e" * 32).decode())}
    )
    client = _FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory,
        TelegramTransport(client),
        configured,
    )
    async with database.session_factory() as session:
        user = await session.get(User, actor_id)
        assert user is not None
        await dispatcher._begin_backup_reconfigure(session, user, profile_id)

    combined = "\n".join(client.messages)
    assert t(LanguageCode.PERSIAN, "edit_keep_hint") in combined
    assert "old-password" not in combined
    assert "old-age-secret" not in combined
    state = await dispatcher.fsm.get(actor_id)
    assert state is not None
    assert "encrypted_password" not in state.data
    assert "encrypted_age_passphrase" not in state.data
    assert t(LanguageCode.ENGLISH, "reconfigure_backup")
    assert t(LanguageCode.PERSIAN, "reconfigure_backup")


async def test_edit_replacement_secret_uses_only_ephemeral_secret_store(
    database, settings: Settings
) -> None:
    encoded_key = base64.b64encode(b"e" * 32).decode()
    configured = settings.model_copy(update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(encoded_key)})
    client = _FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory,
        TelegramTransport(client),
        configured,
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_DATABASE_PASSWORD,
            {"preservable_database": True, "can_preserve_database_password": True},
        )
        await dispatcher._flow(session, actor, "replacement-secret", None, item)
        assert "replacement-secret" not in repr(item.data)
        assert await dispatcher.fsm.secrets.contains(actor.id, "db-auth")
        await dispatcher.fsm.clear(actor.id)

        item = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_DATABASE_PASSWORD,
            {"preservable_database": True, "can_preserve_database_password": True},
        )
        await dispatcher._flow(session, actor, "-", None, item)
        assert item.data["preserve_database_password"] is True
        assert not await dispatcher.fsm.secrets.contains(actor.id, "db-auth")
