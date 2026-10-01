from contextlib import suppress
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import RPCError

from database.models import User
from domain.enums import (
    EncryptionMode,
    Role,
)
from infrastructure.repositories import (
    AuditRepository,
    BackupRepository,
    UserRepository,
    touch_user,
)
from presentation.telegram.audit import audit_pages
from presentation.telegram.constants import (
    SUPERADMIN_ACTIONS,
)
from presentation.telegram.formatting import TelegramHtml, code
from presentation.telegram.i18n import action_for_text, t
from presentation.telegram.keyboards import (
    cancel_back,
    language_menu,
    main_menu,
)
from presentation.telegram.runtime import Conversation, State
from presentation.telegram.wizards import WizardMixin

log = structlog.get_logger()


class RoutingMixin(WizardMixin):
    async def handle(self, event: Any) -> None:
        """Dispatch an event and always acknowledge callback queries."""
        try:
            await self._handle_event(event)
        finally:
            if isinstance(getattr(event, "data", None), bytes):
                answer = getattr(event, "answer", None)
                if callable(answer):
                    with suppress(Exception):
                        await answer()

    async def _handle_event(self, event: Any) -> None:
        """Apply the silent allowlist gate before messages and callback queries."""
        if not bool(event.is_private):
            return
        telegram_id = event.sender_id
        if not isinstance(telegram_id, int) or telegram_id <= 0:
            return
        async with self.sessions() as session:
            user = await UserRepository(session).by_telegram_id(telegram_id, enabled_only=True)
            if user is None:
                await AuditRepository(session).record("unauthorized_interaction_attempt")
                await session.commit()
                return
            if not await self.rate_limit.allow(user.id):
                await AuditRepository(session).record("rate_limit_exceeded", actor_id=user.id)
                await session.commit()
                return
            await touch_user(user)
            data = getattr(event, "data", None)
            if isinstance(data, bytes):
                await self._callback(session, user, data)
            else:
                incoming = event.raw_text if isinstance(event.raw_text, str) else ""
                conversation = await self.fsm.get(user.id)
                if conversation is not None and self._contains_secret(conversation):
                    delete_message = getattr(event, "delete", None)
                    if callable(delete_message):
                        try:
                            await delete_message()
                        except RPCError:
                            await self.fsm.clear(user.id)
                            await self._send(user, "invalid")
                            return
                await self._dispatch(session, user, incoming.strip())
            await session.commit()

    async def _callback(self, session: AsyncSession, user: User, data: bytes) -> None:
        conversation = await self.fsm.get(user.id)
        current_state = conversation.state.value if conversation is not None else None
        if user.role is not Role.SUPERADMIN:
            log.info(
                "telegram_callback_validation",
                user_id=user.id,
                callback_data="opaque",
                current_state=current_state,
                expected_state=None,
                validation_result="authorization_denied",
            )
            await AuditRepository(session).record("authorization_denied", actor_id=user.id)
            return
        callback, callback_result = await self.callbacks.resolve_detailed(user.id, data)
        if callback is None:
            log.info(
                "telegram_callback_validation",
                user_id=user.id,
                callback_data="opaque",
                current_state=current_state,
                expected_state=None,
                validation_result="invalid_or_stale",
            )
            await AuditRepository(session).record("invalid_callback", actor_id=user.id)
            return
        if callback_result == "expired":
            log.info(
                "telegram_callback_validation",
                telegram_user_id=user.telegram_user_id,
                user_id=user.id,
                callback_data=callback.action,
                current_state=current_state,
                expected_state=State.ADD_BACKUP_CONFIRM.value,
                draft_profile_id=None,
                validation_result="expired",
                failure_reason="callback capability expired",
            )
            if callback.action == "backup_confirm":
                await self.fsm.clear(user.id)
                await self._send(user, "backup_setup_expired")
            return
        log.info(
            "telegram_callback_validation",
            user_id=user.id,
            callback_data=callback.action,
            current_state=current_state,
            expected_state=None,
            validation_result="accepted",
        )
        if callback.action in {"backup_confirm", "backup_confirm_cancel"}:
            conversation = await self.fsm.get(user.id)
            if conversation is None:
                log.warning(
                    "backup_profile_confirm_failed",
                    telegram_user_id=user.telegram_user_id,
                    callback_data=callback.action,
                    current_state=None,
                    expected_state=State.ADD_BACKUP_CONFIRM.value,
                    draft_profile_id=callback.target_id or None,
                    validation_result="expired_state",
                    failure_reason="conversation state unavailable",
                )
                await self._send(user, "backup_setup_expired")
                return
            if callback.target_id != conversation.data.get("confirmation_id"):
                log.warning(
                    "backup_profile_confirm_failed",
                    telegram_user_id=user.telegram_user_id,
                    callback_data=callback.action,
                    current_state=conversation.state.value,
                    expected_state=State.ADD_BACKUP_CONFIRM.value,
                    draft_profile_id=conversation.data.get("editing_profile_id"),
                    validation_result="stale_confirmation",
                    failure_reason="callback belongs to an older confirmation rendering",
                )
                await self._send(user, "backup_confirm_stale")
                return
            if callback.action == "backup_confirm_cancel":
                await self.fsm.clear(user.id)
                await self._home(user, "cancelled")
                return
            if conversation.state is not State.ADD_BACKUP_CONFIRM:
                log.warning(
                    "backup_profile_confirm_failed",
                    telegram_user_id=user.telegram_user_id,
                    callback_data=callback.action,
                    current_state=conversation.state.value,
                    expected_state=State.ADD_BACKUP_CONFIRM.value,
                    draft_profile_id=conversation.data.get("editing_profile_id"),
                    validation_result="state_mismatch",
                    failure_reason="conversation is not at profile confirmation",
                )
                await self._send(user, "backup_confirm_invalid_state")
                return
            await self._confirm_backup_profile(session, user, conversation, "confirm")
        elif callback.action == "admin_edit":
            await self._begin_admin_edit(user, callback.target_id)
        elif callback.action == "admin_delete":
            await self._begin_admin_delete(session, user, callback.target_id)
        elif callback.action == "admin_access":
            await self._begin_admin_access(session, user, callback.target_id)
        elif callback.action == "backup_edit":
            await self._begin_backup_edit(user, callback.target_id)
        elif callback.action == "backup_delete":
            await self._begin_backup_delete(session, user, callback.target_id)
        elif callback.action == "backup_access":
            await self._begin_backup_access(session, user, callback.target_id)
        elif callback.action == "server_edit":
            await self._begin_server_edit(user, callback.target_id)
        elif callback.action == "server_delete":
            await self._begin_server_delete(session, user, callback.target_id)
        elif callback.action == "server_test":
            await self._test_server(session, user, callback.target_id)
        elif callback.action == "run_detail":
            await self._show_run_details(session, user, callback.target_id)
        elif callback.action == "run_history_page":
            await self._show_run_history(session, user, callback.target_id)
        elif callback.action == "run_cancel":
            await self._cancel_run(session, user, callback.target_id)
        elif callback.action == "run_retry":
            await self._retry_run(session, user, callback.target_id)
        elif callback.action == "run_purge":
            await self._confirm_run_artifact_purge(session, user, callback.target_id)
        elif callback.action == "run_purge_confirm":
            await self._purge_run_artifact(session, user, callback.target_id)

    @staticmethod
    def _contains_secret(item: Conversation) -> bool:
        if item.state in {
            State.ADD_SERVER_PRIVATE_KEY,
            State.ADD_SERVER_PASSPHRASE,
            State.ADD_BACKUP_DATABASE_PASSWORD,
        }:
            return True
        return (
            item.state is State.ADD_BACKUP_ENCRYPTION_SECRET
            and item.data.get("encryption_mode") is EncryptionMode.AGE_PASSPHRASE
        )

    async def _dispatch(self, session: AsyncSession, user: User, text: str) -> None:
        language = user.language_code
        action = action_for_text(text)
        if text == "/cancel" or action == "cancel":
            await self.fsm.clear(user.id)
            await self._home(user, "cancelled")
            return
        if text == "/start" or action == "home":
            await self.fsm.clear(user.id)
            await self._home(user, "welcome")
            return
        conversation = await self.fsm.get(user.id)
        if conversation is not None:
            if action == "back":
                await self._back(session, user)
            else:
                await self._flow(session, user, text, action, conversation)
            return
        if action == "back":
            await self._home(user, "returned")
            return
        if action in SUPERADMIN_ACTIONS and user.role is not Role.SUPERADMIN:
            await AuditRepository(session).record("authorization_denied", actor_id=user.id)
            await self._home(user, "permission_invalid")
            return
        if action == "language":
            await self.transport.send_text(
                user.telegram_user_id,
                t(language, "language_prompt"),
                buttons=language_menu(language),
            )
        elif action in {"english", "persian"}:
            await self._set_language(session, user, action)
        elif action in {"admins", "refresh_admins"}:
            await self._show_admins(session, user)
        elif action in {"backups", "refresh_backups"}:
            await self._show_backups(session, user)
        elif action == "active_runs":
            await self._show_active_runs(session, user)
        elif action == "run_history":
            await self._show_run_history(session, user, 0)
        elif action in {"remote_servers", "refresh_servers"}:
            await self._show_servers(session, user)
        elif action == "add_admin":
            await self.fsm.set(user.id, State.ADD_ADMIN_NAME)
            await self._send(user, "ask_admin_name", buttons=cancel_back(language))
        elif action == "edit_admin":
            await self._select_admin(session, user, State.SELECT_ADMIN_EDIT, "select_admin_edit")
        elif action == "delete_admin":
            await self._select_admin(
                session, user, State.SELECT_ADMIN_DELETE, "select_admin_delete"
            )
        elif action == "add_backup":
            await self.fsm.set(user.id, State.ADD_BACKUP_NAME)
            await self._send(user, "ask_backup_name", buttons=cancel_back(language))
        elif action == "add_server":
            if self.cipher is None:
                await self._send(user, "invalid")
                return
            await self.fsm.set(user.id, State.ADD_SERVER_NAME)
            await self._send(user, "ask_server_name", buttons=cancel_back(language))
        elif action == "edit_server":
            await self._select_server(session, user, State.SELECT_SERVER_EDIT, "edit_server")
        elif action == "delete_server":
            await self._select_server(session, user, State.SELECT_SERVER_DELETE, "delete_server")
        elif action == "edit_backup":
            await self._select_backup(session, user, State.SELECT_BACKUP_EDIT, "select_backup_edit")
        elif action == "delete_backup":
            await self._select_backup(
                session, user, State.SELECT_BACKUP_DELETE, "select_backup_delete"
            )
        elif action == "my_backups":
            profiles = await BackupRepository(session).list_for_user(user)
            names = "\n".join(f"• {code(profile.name)}" for profile in profiles)
            await self.transport.send_text(
                user.telegram_user_id,
                TelegramHtml(
                    f"{t(language, 'my_backups_title')}\n━━━━━━━━━━━━━━\n"
                    f"{names or t(language, 'none')}\n━━━━━━━━━━━━━━"
                ),
                buttons=main_menu(user.role, language),
            )
        elif action == "audit":
            events = await AuditRepository(session).recent()
            for page in audit_pages(language, events):
                await self.transport.send_text(user.telegram_user_id, page)
        elif action == "settings":
            await self._settings(session, user)
        elif action == "backup_timezone":
            await self.fsm.set(user.id, State.EDIT_BACKUP_TIMEZONE)
            await self._send(user, "ask_backup_timezone", buttons=cancel_back(language))
        elif action == "health":
            await self._health(session, user)
        elif action == "help":
            await self._send(user, "help_text", buttons=main_menu(user.role, language))
        else:
            await self._home(user, "choose_menu")
