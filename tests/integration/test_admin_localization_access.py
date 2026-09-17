from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from application.services import AdminService, BackupService, BootstrapService
from database.session import Database
from domain.enums import LanguageCode, Role, SourceType
from infrastructure.repositories import AccessRepository, UserRepository
from presentation.telegram.i18n.en import STRINGS as ENGLISH_STRINGS
from presentation.telegram.i18n.fa import STRINGS as PERSIAN_STRINGS
from presentation.telegram.keyboards import main_menu


def button_texts(keyboard: list[list[Any]]) -> list[list[str]]:
    return [[button.button.text for button in row] for row in keyboard]


def test_roles_and_bilingual_main_menus() -> None:
    assert set(Role) == {Role.SUPERADMIN, Role.ADMIN}
    assert not hasattr(Role, "USER")
    assert button_texts(main_menu(Role.SUPERADMIN, LanguageCode.ENGLISH)) == [
        ["💾 Backups", "🖥 Remote Servers"],
        ["👥 Admins"],
        ["🧾 Audit Log", "🩺 System Health"],
        ["⚙️ Settings", "🌐 Language"],
        ["ℹ️ Help"],
    ]
    assert button_texts(main_menu(Role.ADMIN, LanguageCode.PERSIAN)) == [
        ["📦 بکاپ‌های من"],
        ["🌐 زبان"],
        ["ℹ️ راهنما"],
    ]
    assert ENGLISH_STRINGS.keys() == PERSIAN_STRINGS.keys()


@pytest.mark.asyncio
async def test_language_defaults_and_persists_across_database_restart(
    session: AsyncSession, settings: Any
) -> None:
    superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    admin = await AdminService(session).create_admin(superadmin, 2002, "recipient", set())
    assert superadmin.language_code is LanguageCode.ENGLISH
    assert admin.language_code is LanguageCode.ENGLISH
    await AdminService(session).change_language(admin, LanguageCode.PERSIAN)
    await session.close()

    restarted = Database(settings.DATABASE_URL)
    try:
        async with restarted.session_factory() as new_session:
            loaded = await UserRepository(new_session).by_telegram_id(2002)
            assert loaded is not None
            assert loaded.language_code is LanguageCode.PERSIAN
    finally:
        await restarted.dispose()


@pytest.mark.asyncio
async def test_bidirectional_access_uses_one_table_and_delivery_rechecks(
    session: AsyncSession,
) -> None:
    superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    first = await BackupService(session).create_draft(
        superadmin, "first", None, SourceType.FILESYSTEM
    )
    second = await BackupService(session).create_draft(
        superadmin, "second", None, SourceType.SERVER_DATABASE
    )
    admin = await AdminService(session).create_admin(superadmin, 2002, "recipient", {first.id})
    access = AccessRepository(session)
    assert await access.profile_ids(admin.id) == {first.id}
    assert await access.admin_ids(first.id) == {admin.id}

    await AdminService(session).assign_admins(superadmin, second.id, {admin.id})
    assert await access.profile_ids(admin.id) == {first.id, second.id}
    await AdminService(session).assign(superadmin, admin.id, {second.id})
    assert await access.admin_ids(first.id) == set()
    assert await access.admin_ids(second.id) == {admin.id}

    recipient = await access.authorized_recipient(2002, second.id)
    assert recipient is not None and recipient.telegram_user_id == 2002
    await AdminService(session).set_enabled(superadmin, admin.id, False)
    assert await access.authorized_recipient(2002, second.id) is None
