from sqlalchemy.ext.asyncio import AsyncSession
from telethon import Button

from application.remote_server_service import RemoteServerService
from database.models import User
from infrastructure.repositories import (
    RemoteServerRepository,
)
from presentation.telegram.admin_management import AdminManagementMixin
from presentation.telegram.formatting import TelegramHtml, code, escaped
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    cancel_back,
    confirmation,
    edit_server_menu,
    remote_servers_menu,
)
from presentation.telegram.runtime import State


class RemoteServersMixin(AdminManagementMixin):
    async def _show_servers(self, session: AsyncSession, user: User) -> None:
        language = user.language_code
        servers = await RemoteServerRepository(session).list_active()
        if not servers:
            await self._send(user, "no_servers", buttons=remote_servers_menu(language))
            return
        for server in servers:
            inline = [
                [
                    Button.inline(
                        t(language, "edit"),
                        await self.callbacks.issue(user.id, "server_edit", server.id),
                    ),
                    Button.inline(
                        t(language, "delete"),
                        await self.callbacks.issue(user.id, "server_delete", server.id),
                    ),
                    Button.inline(
                        t(language, "test_connection"),
                        await self.callbacks.issue(user.id, "server_test", server.id),
                    ),
                ]
            ]
            await self.transport.send_text(
                user.telegram_user_id,
                t(
                    language,
                    "server_card",
                    name=server.name,
                    host=server.host,
                    port=server.ssh_port,
                    username=server.ssh_username,
                    authentication=t(language, "ssh_private_key"),
                    status=t(language, "enabled" if server.enabled else "disabled"),
                    fingerprint=server.host_key_fingerprint,
                ),
                buttons=inline,
            )
        await self._send(user, "servers_title", buttons=remote_servers_menu(language))

    async def _select_server(
        self, session: AsyncSession, user: User, state: State, prompt: str
    ) -> None:
        await self._selection(
            user, state, await RemoteServerRepository(session).list_active(), prompt, "🖥"
        )

    async def _begin_server_edit(self, user: User, target_id: int) -> None:
        await self.fsm.set(user.id, State.EDIT_SERVER_ACTION, {"target_id": target_id})
        await self._send(user, "edit_server_prompt", buttons=edit_server_menu(user.language_code))

    async def _begin_server_reconfigure(
        self, session: AsyncSession, user: User, target_id: int
    ) -> None:
        server = await RemoteServerRepository(session).active_by_id(target_id)
        await self.fsm.set(
            user.id,
            State.ADD_SERVER_NAME,
            {
                "target_id": target_id,
                "name": server.name,
                "host": server.host,
                "port": server.ssh_port,
                "username": server.ssh_username,
            },
        )
        prompt = escaped(t(user.language_code, "ask_server_name_edit"))
        current = escaped(t(user.language_code, "current_value"))
        await self.transport.send_text(
            user.telegram_user_id,
            TelegramHtml(f"{prompt}\n\n{current}\n{code(server.name)}"),
            buttons=cancel_back(user.language_code),
        )

    async def _test_server(self, session: AsyncSession, user: User, target_id: int) -> None:
        if self.cipher is None:
            return
        await RemoteServerService(session, self.cipher).test(user, target_id)
        await self._send(user, "server_test_ok", buttons=remote_servers_menu(user.language_code))

    async def _begin_server_delete(self, session: AsyncSession, user: User, target_id: int) -> None:
        server = await RemoteServerRepository(session).active_by_id(target_id)
        await self.fsm.set(user.id, State.CONFIRM_SERVER_DELETE, {"target_id": target_id})
        await self.transport.send_text(
            user.telegram_user_id,
            t(
                user.language_code,
                "server_card",
                name=server.name,
                host=server.host,
                port=server.ssh_port,
                username=server.ssh_username,
                authentication=t(user.language_code, "ssh_private_key"),
                status=t(user.language_code, "enabled" if server.enabled else "disabled"),
                fingerprint=server.host_key_fingerprint,
            ),
            buttons=confirmation(user.language_code, deleting=True),
        )
