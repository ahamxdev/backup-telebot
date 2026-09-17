from sqlalchemy.ext.asyncio import AsyncSession
from telethon import Button

from application.admin_service import AdminService
from database.models import User
from domain.enums import (
    Role,
)
from infrastructure.repositories import (
    AccessRepository,
    BackupRepository,
    UserRepository,
)
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    admins_menu,
    cancel_back,
    confirmation,
    edit_admin_menu,
    language_menu,
)
from presentation.telegram.runtime import Conversation, State
from presentation.telegram.state_helpers import TelegramStateMixin


class AdminManagementMixin(TelegramStateMixin):
    async def _admin_edit_action(
        self, session: AsyncSession, user: User, item: Conversation, action: str | None
    ) -> None:
        target_id = int(item.data["target_id"])
        if action == "change_name":
            await self.fsm.transition(user.id, item.state, State.EDIT_ADMIN_NAME)
            await self._send(user, "ask_new_name", buttons=cancel_back(user.language_code))
        elif action == "change_telegram_id":
            await self.fsm.transition(user.id, item.state, State.EDIT_ADMIN_TELEGRAM_ID)
            await self._send(user, "ask_new_telegram_id", buttons=cancel_back(user.language_code))
        elif action == "change_language":
            await self.fsm.transition(user.id, item.state, State.EDIT_ADMIN_LANGUAGE)
            await self._send(user, "language_prompt", buttons=language_menu(user.language_code))
        elif action == "backup_access":
            await self._prepare_admin_access(session, item, target_id)
            await self.fsm.transition(user.id, item.state, State.EDIT_ADMIN_ACCESS)
            await self._access_keyboard(user, item, admin_direction=True)
        elif action == "toggle_enabled":
            target = await UserRepository(session).by_id(target_id)
            if target is None or target.role is not Role.ADMIN:
                raise ValueError("not an admin")
            await AdminService(session).set_enabled(user, target_id, not target.enabled)
            await self.fsm.clear(user.id)
            await self._send(user, "admin_status_changed", buttons=admins_menu(user.language_code))
            await self._show_admins(session, user)
        else:
            raise ValueError("unknown action")

    async def _show_admins(self, session: AsyncSession, user: User) -> None:
        language = user.language_code
        admins = await UserRepository(session).list_admins()
        if not admins:
            await self._send(user, "no_admins", buttons=admins_menu(language))
            return
        access = AccessRepository(session)
        backups = {profile.id: profile for profile in await BackupRepository(session).list_active()}
        for admin in admins:
            names = [
                backups[item].name for item in await access.profile_ids(admin.id) if item in backups
            ]
            inline = [
                [
                    Button.inline(
                        t(language, "edit"),
                        await self.callbacks.issue(user.id, "admin_edit", admin.id),
                    ),
                    Button.inline(
                        t(language, "delete"),
                        await self.callbacks.issue(user.id, "admin_delete", admin.id),
                    ),
                    Button.inline(
                        t(language, "backup_access"),
                        await self.callbacks.issue(user.id, "admin_access", admin.id),
                    ),
                ]
            ]
            await self.transport.send_text(
                user.telegram_user_id,
                t(
                    language,
                    "admin_card",
                    name=admin.display_name,
                    telegram_id=admin.telegram_user_id,
                    status=t(language, "enabled" if admin.enabled else "disabled"),
                    admin_language=t(language, f"language_{admin.language_code.value}"),
                    backups="\n".join(f"• {name}" for name in names) or t(language, "none"),
                ),
                buttons=inline,
            )
        await self._send(user, "admins_title", buttons=admins_menu(language))

    async def _select_admin(
        self, session: AsyncSession, user: User, state: State, prompt: str
    ) -> None:
        await self._selection(
            user, state, await UserRepository(session).list_admins(), prompt, "👤"
        )

    async def _begin_admin_edit(self, user: User, target_id: int) -> None:
        await self.fsm.set(user.id, State.EDIT_ADMIN_ACTION, {"target_id": target_id})
        await self._send(user, "edit_admin_prompt", buttons=edit_admin_menu(user.language_code))

    async def _begin_admin_delete(self, session: AsyncSession, user: User, target_id: int) -> None:
        target = await UserRepository(session).by_id(target_id)
        if target is None or target.deleted_at is not None or target.role is not Role.ADMIN:
            return
        await self.fsm.set(user.id, State.CONFIRM_ADMIN_DELETE, {"target_id": target_id})
        await self._send(
            user,
            "admin_delete_summary",
            name=target.display_name,
            telegram_id=target.telegram_user_id,
            status=t(user.language_code, "enabled" if target.enabled else "disabled"),
            admin_language=t(user.language_code, f"language_{target.language_code.value}"),
            buttons=confirmation(user.language_code, deleting=True),
        )

    async def _begin_admin_access(self, session: AsyncSession, user: User, target_id: int) -> None:
        item = await self.fsm.set(user.id, State.EDIT_ADMIN_ACCESS, {"target_id": target_id})
        await self._prepare_admin_access(session, item, target_id)
        await self._access_keyboard(user, item, admin_direction=True)
