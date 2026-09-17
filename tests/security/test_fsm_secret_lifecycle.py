import base64
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    ScheduleConfig,
)
from application.remote_server_service import PendingRemoteServer
from application.services import BootstrapService
from config.settings import Settings
from database.models import BackupProfile, DatabaseSource
from database.session import Database
from domain.enums import (
    CompressionFormat,
    DatabaseEngine,
    EncryptionMode,
    ScheduleKind,
    SourceType,
    TLSMode,
)
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram import dispatcher as dispatcher_module
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.runtime import callbacks as callback_module
from presentation.telegram.runtime import fsm as fsm_module
from presentation.telegram.runtime.fsm import State


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)


@dataclass
class Event:
    sender_id: int
    raw_text: str
    is_private: bool = True
    deleted: bool = False

    async def delete(self) -> None:
        self.deleted = True


def configured_settings(settings: Settings) -> Settings:
    key = base64.b64encode(b"s" * 32).decode()
    return settings.model_copy(update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(key)})


async def dispatcher_with_superadmin(
    database: Database, settings: Settings
) -> tuple[TelegramDispatcher, FakeClient, int]:
    async with database.session_factory() as session:
        actor, _created = await BootstrapService(session).bootstrap_superadmin(1001)
        actor_id = actor.id
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured_settings(settings)
    )
    return dispatcher, client, actor_id


def assert_not_in_fsm(secret: str, dispatcher: TelegramDispatcher, actor_id: int) -> None:
    conversation = dispatcher.fsm._states.get(actor_id)
    assert conversation is not None
    assert secret not in repr(conversation.data)
    assert all(
        secret.encode() not in item.ciphertext for item in dispatcher.fsm.secrets._items.values()
    )


async def test_database_password_moves_immediately_to_encrypted_secret_store(
    database: Database, settings: Settings
) -> None:
    dispatcher, _client, actor_id = await dispatcher_with_superadmin(database, settings)
    secret = "database-secret-value"
    await dispatcher.fsm.set(actor_id, State.ADD_BACKUP_DATABASE_PASSWORD)

    event = Event(1001, secret)
    await dispatcher.handle(event)

    assert event.deleted
    conversation = await dispatcher.fsm.get(actor_id)
    assert conversation is not None
    assert conversation.state is State.ADD_BACKUP_DATABASE_TLS
    assert_not_in_fsm(secret, dispatcher, actor_id)
    assert await dispatcher.fsm.secrets.contains(actor_id, "db-auth")


async def test_ssh_key_and_passphrase_are_removed_after_probe(
    database: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher, _client, actor_id = await dispatcher_with_superadmin(database, settings)
    private_key = "private-key-secret-value"
    passphrase = "private-key-unlock-value"
    await dispatcher.fsm.set(
        actor_id,
        State.ADD_SERVER_PRIVATE_KEY,
        {"name": "storage", "host": "host.test", "port": 22, "username": "backup"},
    )
    probe = AsyncMock(
        return_value=PendingRemoteServer(
            "storage", "host.test", 22, "backup", b"encrypted-key", b"encrypted-unlock", "SHA256:x"
        )
    )
    monkeypatch.setattr(dispatcher_module.RemoteServerService, "probe", probe)

    key_event = Event(1001, private_key)
    await dispatcher.handle(key_event)
    assert key_event.deleted
    assert_not_in_fsm(private_key, dispatcher, actor_id)
    assert await dispatcher.fsm.secrets.contains(actor_id, "ssh-identity")

    passphrase_event = Event(1001, passphrase)
    await dispatcher.handle(passphrase_event)

    assert passphrase_event.deleted
    probe.assert_awaited_once()
    assert probe.await_args.kwargs["private_key"] == private_key
    assert probe.await_args.kwargs["passphrase"] == passphrase
    assert not await dispatcher.fsm.secrets.contains(actor_id)
    conversation = await dispatcher.fsm.get(actor_id)
    assert conversation is not None
    assert private_key not in repr(conversation.data)
    assert passphrase not in repr(conversation.data)


async def test_probe_validation_failure_removes_ssh_secrets(
    database: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher, _client, actor_id = await dispatcher_with_superadmin(database, settings)
    await dispatcher.fsm.set(
        actor_id,
        State.ADD_SERVER_PRIVATE_KEY,
        {"name": "storage", "host": "host.test", "port": 22, "username": "backup"},
    )
    monkeypatch.setattr(
        dispatcher_module.RemoteServerService,
        "probe",
        AsyncMock(side_effect=ValueError("invalid identity")),
    )

    await dispatcher.handle(Event(1001, "private-key-secret-value"))
    await dispatcher.handle(Event(1001, "private-key-unlock-value"))

    assert not await dispatcher.fsm.secrets.contains(actor_id)


async def test_age_passphrase_is_encrypted_then_cancelled_immediately(
    database: Database, settings: Settings
) -> None:
    dispatcher, _client, actor_id = await dispatcher_with_superadmin(database, settings)
    secret = "age-secret-value"
    await dispatcher.fsm.set(
        actor_id,
        State.ADD_BACKUP_ENCRYPTION_SECRET,
        {"encryption_mode": EncryptionMode.AGE_PASSPHRASE},
    )

    event = Event(1001, secret)
    await dispatcher.handle(event)

    assert event.deleted
    assert_not_in_fsm(secret, dispatcher, actor_id)
    assert await dispatcher.fsm.secrets.contains(actor_id, "age-unlock")

    await dispatcher.handle(Event(1001, "/cancel"))

    assert await dispatcher.fsm.get(actor_id) is None
    assert not await dispatcher.fsm.secrets.contains(actor_id)


async def test_periodic_prune_removes_expired_fsm_secrets_and_callbacks(
    database: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    monkeypatch.setattr(fsm_module, "monotonic", lambda: clock)
    monkeypatch.setattr(callback_module, "monotonic", lambda: clock)
    dispatcher, _client, actor_id = await dispatcher_with_superadmin(database, settings)
    await dispatcher.fsm.set(actor_id, State.ADD_BACKUP_DATABASE_PASSWORD)
    await dispatcher.fsm.secrets.put(actor_id, "db-auth", "expiring-secret")
    await dispatcher.callbacks.issue(actor_id, "backup_edit", 1)
    clock = settings.FSM_TIMEOUT_SECONDS + 1.0

    fsm_removed, callbacks_removed = await dispatcher.prune_expired_state()

    assert (fsm_removed, callbacks_removed) == (1, 1)
    assert await dispatcher.fsm.get(actor_id) is None
    assert not await dispatcher.fsm.secrets.contains(actor_id)


async def test_successful_profile_completion_leaves_no_plaintext_or_secret_state(
    database: Database, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    dispatcher, client, actor_id = await dispatcher_with_superadmin(database, settings)
    database_secret = "completion-database-secret"
    age_secret = "completion-age-secret"
    data = {
        "name": "completed-profile",
        "description": None,
        "source_type": SourceType.DATABASE,
        "engine": DatabaseEngine.POSTGRESQL,
        "database_host": "db.test",
        "database_port": 5432,
        "database_name": "application",
        "database_username": "backup",
        "tls_mode": TLSMode.REQUIRED,
        "schedule": ScheduleConfig(ScheduleKind.MANUAL),
        "compression": CompressionConfig(CompressionFormat.NONE, None),
        "encryption": EncryptionConfig(EncryptionMode.AGE_PASSPHRASE),
        "selected": set(),
    }
    await dispatcher.fsm.set(actor_id, State.ADD_BACKUP_CONFIRM, data)
    await dispatcher.fsm.secrets.put(actor_id, "db-auth", database_secret)
    await dispatcher.fsm.secrets.put(actor_id, "age-unlock", age_secret)

    await dispatcher.handle(Event(1001, "✅ Confirm"))

    assert await dispatcher.fsm.get(actor_id) is None
    assert not await dispatcher.fsm.secrets.contains(actor_id)
    combined_output = "\n".join(client.messages) + caplog.text
    assert database_secret not in combined_output
    assert age_secret not in combined_output
    async with database.session_factory() as session:
        profile = await session.scalar(
            select(BackupProfile).where(BackupProfile.name == "completed-profile")
        )
        assert profile is not None
        database_source = await session.get(DatabaseSource, profile.id)
        assert database_source is not None
        assert database_secret.encode() not in database_source.encrypted_password
        assert profile.encrypted_age_passphrase is not None
        assert age_secret.encode() not in profile.encrypted_age_passphrase
