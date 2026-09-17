import base64
from typing import Any

from pydantic import SecretStr

from application.bootstrap_service import BootstrapService
from config.settings import Settings
from domain.enums import SourceType
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.runtime import State


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, _entity: int, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)


def configured(settings: Settings) -> Settings:
    key = base64.b64encode(b"b" * 32).decode()
    return settings.model_copy(update={"MASTER_ENCRYPTION_KEY_B64": SecretStr(key)})


async def test_admin_wizard_back_renders_each_real_step(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(actor.id, State.ADD_ADMIN_NAME)
        item.data["name"] = "Ikram"
        await dispatcher.fsm.transition(actor.id, State.ADD_ADMIN_NAME, State.ADD_ADMIN_ID)
        item.data["telegram_id"] = 20930123412
        await dispatcher.fsm.transition(actor.id, State.ADD_ADMIN_ID, State.ADD_ADMIN_LANGUAGE)

        await dispatcher._back(session, actor)
        assert "Enter the numeric Telegram ID:" in client.messages[-1]
        assert "20930123412" in client.messages[-1]
        await dispatcher._back(session, actor)
        assert "Enter display name:" in client.messages[-1]
        assert "Ikram" in client.messages[-1]
        await dispatcher._back(session, actor)
        assert "Admin management" in client.messages[-1] or "No admins found" in client.messages[-1]
        assert "Returned home" not in client.messages[-1]


async def test_remote_server_wizard_back_preserves_values(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(actor.id, State.ADD_SERVER_NAME)
        item.data["name"] = "Storage"
        await dispatcher.fsm.transition(actor.id, State.ADD_SERVER_NAME, State.ADD_SERVER_HOST)
        item.data["host"] = "storage.internal"
        await dispatcher.fsm.transition(actor.id, State.ADD_SERVER_HOST, State.ADD_SERVER_PORT)

        await dispatcher._back(session, actor)
        assert "Enter the host name or IP address:" in client.messages[-1]
        assert "storage.internal" in client.messages[-1]
        await dispatcher._back(session, actor)
        assert "Enter a unique server name:" in client.messages[-1]
        assert "Storage" in client.messages[-1]
        await dispatcher._back(session, actor)
        assert (
            "Remote server management" in client.messages[-1]
            or "No remote servers" in client.messages[-1]
        )


async def test_backup_and_edit_wizard_back_render_actual_steps(
    database, settings: Settings
) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        item = await dispatcher.fsm.set(actor.id, State.ADD_BACKUP_NAME)
        item.data.update(
            {"name": "Config", "description": "daily", "source_type": SourceType.DATABASE}
        )
        await dispatcher.fsm.transition(
            actor.id, State.ADD_BACKUP_NAME, State.ADD_BACKUP_DESCRIPTION
        )
        await dispatcher.fsm.transition(
            actor.id, State.ADD_BACKUP_DESCRIPTION, State.ADD_BACKUP_SOURCE
        )
        await dispatcher._back(session, actor)
        assert "Enter a description" in client.messages[-1]
        assert "daily" in client.messages[-1]
        await dispatcher._back(session, actor)
        assert "Enter the backup name:" in client.messages[-1]
        assert "Config" in client.messages[-1]

        await dispatcher.fsm.set(actor.id, State.EDIT_ADMIN_ACTION, {"target_id": 99})
        await dispatcher.fsm.transition(actor.id, State.EDIT_ADMIN_ACTION, State.EDIT_ADMIN_NAME)
        await dispatcher._back(session, actor)
        assert client.messages[-1] == "Choose what to edit:"


async def test_back_navigation_never_renders_secret_values(database, settings: Settings) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(client), configured(settings)
    )
    secret = "PRIVATE-KEY-SECRET-MUST-NOT-RENDER"
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        await dispatcher.fsm.set(actor.id, State.ADD_SERVER_PRIVATE_KEY)
        await dispatcher.fsm.secrets.put(actor.id, "ssh-identity", secret)
        await dispatcher.fsm.transition(
            actor.id, State.ADD_SERVER_PRIVATE_KEY, State.ADD_SERVER_PASSPHRASE
        )
        await dispatcher._back(session, actor)

    assert secret not in client.messages[-1]
    assert "Existing secret is set" in client.messages[-1]
    assert "Returned to the previous step" not in "\n".join(client.messages)
