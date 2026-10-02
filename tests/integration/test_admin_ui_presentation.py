import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from application.admin_service import AdminService
from application.backup_service import BackupService
from application.bootstrap_service import BootstrapService
from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    FilesConfig,
    ScheduleConfig,
)
from application.remote_server_service import PendingRemoteServer
from config.settings import Settings
from database.models import BackupProfile, BackupRun, RemoteServer, User
from domain.enums import (
    CompressionFormat,
    CompressionPreset,
    EncryptionMode,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceLocation,
    SourceType,
)
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.runtime import State
from presentation.telegram.runtime import callbacks as callback_module
from presentation.telegram.runtime import fsm as fsm_module


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)

    async def send_file(self, _entity: int, _file: str, **_kwargs: Any) -> None:
        raise AssertionError("not expected")


def valid_confirmation_data(confirmation_id: int) -> dict[str, Any]:
    return {
        "confirmation_id": confirmation_id,
        "name": "callback profile",
        "description": None,
        "source_type": SourceType.FILES_DIRECTORIES,
        "schedule": ScheduleConfig(ScheduleKind.MANUAL),
        "compression": CompressionConfig(CompressionFormat.NONE, None),
        "encryption": EncryptionConfig(EncryptionMode.NONE),
        "selected": set(),
        "location": SourceLocation.LOCAL,
        "paths": ("/srv/backups/callback",),
    }


@pytest.mark.asyncio
async def test_valid_inline_confirm_callback_creates_profile(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        confirmation_id = 123456
        await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_CONFIRM,
            valid_confirmation_data(confirmation_id),
        )
        token = await dispatcher.callbacks.issue(actor.id, "backup_confirm", confirmation_id)

        await dispatcher._callback(session, actor, token)

        assert await dispatcher.fsm.get(actor.id) is None
        assert await session.scalar(
            BackupProfile.__table__.select().where(BackupProfile.name == "callback profile")
        )
    assert any("callback profile" in message for message in client.messages)


@pytest.mark.asyncio
async def test_expired_inline_confirm_callback_reports_expiration(
    database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = 0.0
    monkeypatch.setattr(fsm_module, "monotonic", lambda: clock)
    monkeypatch.setattr(callback_module, "monotonic", lambda: clock)
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        confirmation_id = 789012
        await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_CONFIRM,
            valid_confirmation_data(confirmation_id),
        )
        token = await dispatcher.callbacks.issue(actor.id, "backup_confirm", confirmation_id)
        clock = settings.FSM_TIMEOUT_SECONDS + 1.0

        await dispatcher._callback(session, actor, token)

        assert await dispatcher.fsm.get(actor.id) is None
    assert client.messages[-1] == (
        "⌛ <b>Setup Expired</b>\nThis session expired. Please restart backup profile creation."
    )


@pytest.mark.asyncio
async def test_confirm_during_active_run_is_not_rejected_as_a_run_conflict(
    database, settings: Settings
) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await BackupService(session).create_draft(
            actor, "busy", None, SourceType.FILES_DIRECTORIES
        )
        run = BackupRun(
            profile_id=profile.id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.RUNNING,
            stage=RunStage.COMPRESS,
        )
        session.add(run)
        await session.commit()
        item = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_CONFIRM,
            {"editing_profile_id": profile.id},
        )

        await dispatcher._flow(session, actor, "✅ Confirm", "confirm", item)

    assert "⚠️ Backup Is Running" not in client.messages[-1]
    assert "Missing field: <code>name</code>" in client.messages[-1]
    conversation = await dispatcher.fsm.get(actor.id)
    assert conversation is not None
    assert conversation.state is State.ADD_BACKUP_CONFIRM
    assert conversation.invalid_inputs == 0


def configured(settings: Settings) -> Settings:
    key = base64.b64encode(b"u" * 32).decode()
    return settings.model_copy(update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(key)})


@pytest.mark.asyncio
async def test_detail_cards_show_safe_configuration_with_icons(
    database, settings: Settings
) -> None:
    secret_markers = ("SSH_PRIVATE_SECRET", "SSH_PASSPHRASE_SECRET", "AGE_SECRET")
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        recipient = await AdminService(session).create_admin(actor, 2002, "Operations", set())
        server = RemoteServer(
            name="Git",
            host="192.0.2.19",
            ssh_port=22422,
            ssh_username="root",
            encrypted_private_key=secret_markers[0].encode(),
            encrypted_key_passphrase=secret_markers[1].encode(),
            host_key_fingerprint="SHA256:xxxx",
            enabled=True,
        )
        session.add(server)
        await session.commit()
        profile = await BackupService(
            session,
            TelegramDispatcher(
                database.session_factory, TelegramTransport(FakeClient()), configured(settings)
            ).cipher,
            allowed_local_roots=("/srv",),
        ).create_files_profile(
            actor,
            name="git backup",
            description=None,
            files=FilesConfig(SourceLocation.REMOTE_SERVER, ("/srv/git",), server.id),
            schedule=ScheduleConfig(ScheduleKind.EVERY_N_MINUTES, interval=5),
            compression=CompressionConfig(CompressionFormat.ZSTANDARD, CompressionPreset.BALANCED),
            encryption=EncryptionConfig(EncryptionMode.AGE_PASSPHRASE, passphrase="AGE_SECRET"),
            admin_ids={recipient.id},
        )
        assert profile.id

    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor = await session.get(User, 1)
        assert actor is not None
        await dispatcher._show_servers(session, actor)
        await dispatcher._show_backups(session, actor)
        await dispatcher._show_admins(session, actor)

    rendered = "\n".join(client.messages)
    for expected in (
        "🖥 <b>Remote Server Details</b>",
        "📌 <b>Server Name:</b> <code>Git</code>",
        "🌐 <b>Address:</b> <code>192.0.2.19:22422</code>",
        "👤 <b>User:</b> <code>root</code>",
        "🔐 <b>Authentication:</b> SSH private key",
        "🛡 <b>Fingerprint:</b> <code>SHA256:xxxx</code>",
        "📌 <b>Name:</b> <code>git backup</code>",
        "📁 <b>Source:</b> 📁 Files & Directories",
        "🖥 <b>Remote Server:</b> <code>Git</code>",
        "⏰ <b>Schedule:</b> Every 5 minutes",
        "🗜 <b>Compression</b>\n<b>Algorithm:</b> Zstandard",
        "<b>Extension:</b> <code>.zst</code>",
        "📦 <b>Archive</b>\n<b>Format:</b> TAR",
        "<b>Output:</b> <code>backup.tar.zst</code>",
        "🔐 <b>Encryption:</b> Age Passphrase",
        "👥 <b>Access:</b> • <code>Operations</code>",
        "👤 <b>Admin Details</b>",
        "🆔 <b>Telegram ID:</b> <code>2002</code>",
        "📦 <b>Allowed Backups:</b> • git backup",
    ):
        assert expected in rendered
    for secret in secret_markers:
        assert secret not in rendered


@pytest.mark.asyncio
async def test_remote_server_add_edit_delete_refresh_the_existing_list_handler(
    database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(FakeClient()), configured(settings)
    )
    refresh = AsyncMock()
    monkeypatch.setattr(dispatcher, "_show_servers", refresh)
    pending = PendingRemoteServer(
        "storage", "storage.internal", 22, "backup", b"encrypted", None, "SHA256:test"
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(
            actor.id, State.CONFIRM_SERVER_FINGERPRINT, {"pending": pending}
        )
        await dispatcher._flow(session, actor, "", "confirm", item)
        assert refresh.await_count == 1

        server = (await session.execute(RemoteServer.__table__.select())).first()
        assert server is not None
        item = await dispatcher.fsm.set(
            actor.id, State.EDIT_SERVER_ACTION, {"target_id": server.id}
        )
        await dispatcher._flow(session, actor, "", "toggle_enabled", item)
        assert refresh.await_count == 2

        item = await dispatcher.fsm.set(
            actor.id, State.CONFIRM_SERVER_DELETE, {"target_id": server.id}
        )
        await dispatcher._flow(session, actor, "", "confirm_delete", item)
        assert refresh.await_count == 3


@pytest.mark.asyncio
async def test_backup_add_edit_delete_refresh_the_existing_list_handler(
    database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(FakeClient()), configured(settings)
    )
    refresh = AsyncMock()
    monkeypatch.setattr(dispatcher, "_show_backups", refresh)
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await BackupService(session).create_draft(
            actor, "before", None, SourceType.FILES_DIRECTORIES
        )
        monkeypatch.setattr(dispatcher, "_persist_profile", AsyncMock(return_value=profile))
        item = await dispatcher.fsm.set(
            actor.id,
            State.ADD_BACKUP_CONFIRM,
            {
                "name": "before",
                "source_type": SourceType.FILES_DIRECTORIES,
                "schedule": ScheduleConfig(ScheduleKind.MANUAL),
                "compression": CompressionConfig(CompressionFormat.NONE, None),
                "encryption": EncryptionConfig(EncryptionMode.NONE),
                "selected": set(),
                "location": SourceLocation.LOCAL,
                "paths": ("/srv/backups",),
            },
        )
        await dispatcher._flow(session, actor, "", "confirm", item)
        assert refresh.await_count == 1

        item = await dispatcher.fsm.set(actor.id, State.EDIT_BACKUP_NAME, {"target_id": profile.id})
        await dispatcher._flow(session, actor, "after", None, item)
        assert refresh.await_count == 2

        item = await dispatcher.fsm.set(
            actor.id, State.CONFIRM_BACKUP_DELETE, {"target_id": profile.id}
        )
        await dispatcher._flow(session, actor, "", "confirm_delete", item)
        assert refresh.await_count == 3


@pytest.mark.asyncio
async def test_admin_add_edit_delete_refresh_the_existing_list_handler(
    database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(FakeClient()), configured(settings)
    )
    refresh = AsyncMock()
    monkeypatch.setattr(dispatcher, "_show_admins", refresh)
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        admin = await AdminService(session).create_admin(actor, 2002, "before", set())
        monkeypatch.setattr(dispatcher, "_handle_access_action", AsyncMock(return_value=True))
        item = await dispatcher.fsm.set(actor.id, State.ADD_ADMIN_ACCESS, {})
        await dispatcher._flow(session, actor, "", "save_access", item)
        assert refresh.await_count == 1

        item = await dispatcher.fsm.set(actor.id, State.EDIT_ADMIN_NAME, {"target_id": admin.id})
        await dispatcher._flow(session, actor, "after", None, item)
        assert refresh.await_count == 2

        item = await dispatcher.fsm.set(
            actor.id, State.CONFIRM_ADMIN_DELETE, {"target_id": admin.id}
        )
        await dispatcher._flow(session, actor, "", "confirm_delete", item)
        assert refresh.await_count == 3
