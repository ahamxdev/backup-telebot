from dataclasses import dataclass
from typing import Any

import pytest

from application.services import AdminService, BackupService, BootstrapService
from config.settings import Settings
from database.session import Database
from domain.enums import LanguageCode, SourceType
from infrastructure.repositories import UserRepository
from infrastructure.telegram.transport import TelegramTransport
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.runtime.fsm import State


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, Any]] = []

    async def send_message(self, entity: int, message: str, **kwargs: Any) -> None:
        self.messages.append((entity, message, kwargs.get("buttons")))

    async def send_file(self, entity: int, file: str, **kwargs: Any) -> None:
        raise AssertionError("not expected")


@dataclass
class Event:
    sender_id: int
    raw_text: str
    is_private: bool = True


@dataclass
class CallbackEvent:
    sender_id: int
    data: bytes
    is_private: bool = True
    answered: bool = False

    async def answer(self) -> None:
        self.answered = True


@dataclass
class DeletableEvent(Event):
    deleted: bool = False

    async def delete(self) -> None:
        self.deleted = True


@pytest.mark.asyncio
async def test_unknown_group_and_disabled_are_silent(
    database: Database, settings: Settings
) -> None:
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    unknown_callback = CallbackEvent(9999, b"v1:unknown")
    await dispatcher.handle(unknown_callback)
    assert unknown_callback.answered
    await dispatcher.handle(Event(9999, "/start"))
    await dispatcher.handle(Event(9999, "💾 بکاپ‌ها"))
    await dispatcher.handle(Event(9999, "file", False))
    async with database.session_factory() as session:
        admin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        user = await AdminService(session).create_admin(admin, 2002, "off", set())
        await AdminService(session).set_enabled(admin, user.id, False)
    await dispatcher.handle(Event(2002, "/start"))
    assert client.messages == []


@pytest.mark.asyncio
async def test_admin_cannot_spoof_superadmin_button(database: Database, settings: Settings) -> None:
    async with database.session_factory() as session:
        admin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        await AdminService(session).create_admin(admin, 2002, "admin", set())
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    await dispatcher.handle(Event(2002, "➕ Add Admin"))
    assert len(client.messages) == 1
    assert "not available" in client.messages[0][1]


@pytest.mark.asyncio
async def test_unexpected_button_is_consumed_by_fsm(database: Database, settings: Settings) -> None:
    async with database.session_factory() as session:
        await BootstrapService(session).bootstrap_superadmin(1001)
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    await dispatcher.handle(Event(1001, "➕ افزودن بکاپ"))
    await dispatcher.handle(Event(1001, "🗑 Delete Admin"))
    state = await dispatcher.fsm.get(1)
    assert state is not None
    assert state.data["name"] == "🗑 Delete Admin"


@pytest.mark.asyncio
async def test_sensitive_input_is_deleted_from_telegram(
    database: Database, settings: Settings
) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    await dispatcher.fsm.set(superadmin.id, State.ADD_SERVER_PRIVATE_KEY)
    event = DeletableEvent(1001, "private key material")
    await dispatcher.handle(event)
    assert event.deleted
    state = await dispatcher.fsm.get(superadmin.id)
    assert state is not None
    assert state.state is State.ADD_SERVER_PASSPHRASE


@pytest.mark.asyncio
async def test_language_switch_is_immediate_and_persisted(
    database: Database, settings: Settings
) -> None:
    async with database.session_factory() as session:
        await BootstrapService(session).bootstrap_superadmin(1001)
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    await dispatcher.handle(Event(1001, "🌐 Language"))
    await dispatcher.handle(Event(1001, "🇮🇷 فارسی"))
    assert client.messages[-1][1] == "زبان بروزرسانی شد."
    async with database.session_factory() as session:
        user = await UserRepository(session).by_telegram_id(1001)
        assert user is not None
        assert user.language_code is LanguageCode.PERSIAN


@pytest.mark.asyncio
async def test_admin_cards_have_bound_opaque_inline_actions(
    database: Database, settings: Settings
) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        backup = await BackupService(session).create_draft(
            superadmin, "Production DB", None, SourceType.SERVER_DATABASE
        )
        first = await AdminService(session).create_admin(
            superadmin, 2002, "Server Team", {backup.id}
        )
        await AdminService(session).create_admin(superadmin, 2003, "Office Admin", set())
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    await dispatcher.handle(Event(1001, "👥 Admins"))
    assert len(client.messages) == 3
    assert "Server Team" in client.messages[0][1]
    assert "Production DB" in client.messages[0][1]
    assert "Office Admin" in client.messages[1][1]
    assert "📦 Allowed Backups\nNone" in client.messages[1][1]

    edit_button = client.messages[0][2][0][0]
    assert edit_button.data.startswith(b"v1:")
    assert b"2002" not in edit_button.data
    accepted = CallbackEvent(1001, edit_button.data)
    await dispatcher.handle(accepted)
    assert accepted.answered
    state = await dispatcher.fsm.get(superadmin.id)
    assert state is not None
    assert state.data["target_id"] == first.id

    before = len(client.messages)
    unauthorized = CallbackEvent(2003, edit_button.data)
    forged = CallbackEvent(1001, b"v1:forged")
    await dispatcher.handle(unauthorized)
    await dispatcher.handle(forged)
    assert unauthorized.answered and forged.answered
    assert len(client.messages) == before


@pytest.mark.asyncio
async def test_callback_is_one_time_and_attacker_cannot_consume_it(
    database: Database, settings: Settings
) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        await AdminService(session).create_admin(superadmin, 2002, "admin", set())
    dispatcher = TelegramDispatcher(
        database.session_factory, TelegramTransport(FakeClient()), settings
    )
    token = await dispatcher.callbacks.issue(superadmin.id, "admin_edit", 2002)

    assert await dispatcher.callbacks.resolve(999, token) is None
    assert await dispatcher.callbacks.resolve(superadmin.id, token) is not None
    assert await dispatcher.callbacks.resolve(superadmin.id, token) is None
