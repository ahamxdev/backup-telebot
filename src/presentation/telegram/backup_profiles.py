import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import Button

from application.backup_service import BackupService
from application.profile_config import CompressionConfig, EncryptionConfig, ScheduleConfig
from application.settings_service import SettingsService
from database.models import User
from domain.enums import SourceLocation
from infrastructure.repositories import (
    AccessRepository,
    BackupRepository,
    RemoteServerRepository,
    UserRepository,
)
from pipeline.jobs import RunService
from pipeline.resources import disk_has_capacity
from presentation.telegram.datetime_format import (
    format_user_datetime,
    format_user_timestamp,
    timezone_label,
)
from presentation.telegram.formatting import TelegramHtml, code
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    backups_menu,
    cancel_back,
    confirmation,
    edit_backup_menu,
)
from presentation.telegram.profile_summary import file_source_path_lines
from presentation.telegram.remote_servers import RemoteServersMixin
from presentation.telegram.runtime import Conversation, State


class BackupProfilesMixin(RemoteServersMixin):
    async def _backup_edit_action(
        self, session: AsyncSession, user: User, item: Conversation, action: str | None
    ) -> None:
        target_id = int(item.data["target_id"])
        if action == "change_name":
            await self.fsm.transition(user.id, item.state, State.EDIT_BACKUP_NAME)
            await self._send(user, "ask_new_name", buttons=cancel_back(user.language_code))
        elif action == "change_description":
            await self.fsm.transition(user.id, item.state, State.EDIT_BACKUP_DESCRIPTION)
            await self._send(user, "ask_description", buttons=cancel_back(user.language_code))
        elif action == "reconfigure_backup":
            await self._begin_backup_reconfigure(session, user, target_id)
        elif action == "admin_access":
            await self._prepare_backup_access(session, item, target_id)
            await self.fsm.transition(user.id, item.state, State.BACKUP_ADMIN_ACCESS)
            await self._access_keyboard(user, item, admin_direction=False)
        elif action == "toggle_enabled":
            target = await BackupRepository(session).by_id(target_id)
            if target is None or target.archived_at is not None:
                raise ValueError("not found")
            await BackupService(session).edit(user, target_id, enabled=not target.enabled)
            await self.fsm.clear(user.id)
            await self._send(
                user, "backup_status_changed", buttons=backups_menu(user.language_code)
            )
            await self._show_backups(session, user)
        elif action == "run_backup_now":
            service = RunService(session)
            if not disk_has_capacity(self.settings, await service.pending_bytes()):
                await self._send(
                    user, "backup_backpressure", buttons=backups_menu(user.language_code)
                )
                return
            result = await service.enqueue_manual(user, target_id)
            await self.fsm.clear(user.id)
            if result.created:
                await self._send(
                    user,
                    "backup_run_queued",
                    run_id=result.run.id,
                    buttons=backups_menu(user.language_code),
                )
            else:
                timezone = (await SettingsService(session).get()).backup_timezone
                started = format_user_datetime(result.run.started_at, timezone)
                await self._send(
                    user,
                    "backup_run_active",
                    stage=result.run.stage.value,
                    started=started,
                    buttons=backups_menu(user.language_code),
                )
        else:
            raise ValueError("unknown action")

    async def _show_backups(self, session: AsyncSession, user: User) -> None:
        language = user.language_code
        timezone = (await SettingsService(session).get()).backup_timezone
        active = await RunService(session).list_active(user)
        await self._send(
            user,
            "active_runs_overview" if active else "no_active_operations",
            count=len(active),
        )
        profiles = await BackupRepository(session).list_active()
        if not profiles:
            await self._send(user, "no_backups", buttons=backups_menu(language))
            return
        users = {admin.id: admin for admin in await UserRepository(session).list_admins()}
        servers = {
            server.id: server for server in await RemoteServerRepository(session).list_active()
        }
        access = AccessRepository(session)
        for profile in profiles:
            await session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
            source_details = TelegramHtml("")
            if profile.files_source is not None:
                await session.refresh(profile.files_source, ("paths",))
                paths = file_source_path_lines(
                    language, (path.path for path in profile.files_source.paths)
                )
                location_key = (
                    "source_location_local"
                    if profile.files_source.location is SourceLocation.LOCAL
                    else "source_location_remote"
                )
                source_details = t(
                    language,
                    "file_source_details",
                    location=t(language, location_key),
                    paths=paths,
                )
            elif profile.gitlab_source is not None:
                location_key = (
                    "source_location_local"
                    if profile.gitlab_source.location is SourceLocation.LOCAL
                    else "source_location_remote"
                )
                source_details = t(
                    language,
                    "gitlab_source_details",
                    location=t(language, location_key),
                    container=code(profile.gitlab_source.container_name),
                    backup_path=code(profile.gitlab_source.backup_path),
                )
            elif profile.database_source is not None:
                source_details = t(
                    language,
                    "database_source_details",
                    engine=self._engine_label(language, profile.database_source.engine),
                )
            names = [
                users[item].display_name
                for item in await access.admin_ids(profile.id)
                if item in users
            ]
            remote_server_id = None
            if (
                profile.files_source is not None
                and profile.files_source.location is SourceLocation.REMOTE_SERVER
            ):
                remote_server_id = profile.files_source.remote_server_id
            elif profile.database_source is not None:
                remote_server_id = profile.database_source.artifact_remote_server_id
            elif (
                profile.gitlab_source is not None
                and profile.gitlab_source.location is SourceLocation.REMOTE_SERVER
            ):
                remote_server_id = profile.gitlab_source.remote_server_id
            remote_server = servers.get(remote_server_id) if remote_server_id is not None else None
            inline = [
                [
                    Button.inline(
                        t(language, "edit"),
                        await self.callbacks.issue(user.id, "backup_edit", profile.id),
                    ),
                    Button.inline(
                        t(language, "delete"),
                        await self.callbacks.issue(user.id, "backup_delete", profile.id),
                    ),
                    Button.inline(
                        t(language, "admin_access"),
                        await self.callbacks.issue(user.id, "backup_access", profile.id),
                    ),
                ]
            ]
            await self.transport.send_text(
                user.telegram_user_id,
                t(
                    language,
                    "backup_card",
                    name=profile.name,
                    source=self._source_label(language, profile.source_type),
                    server=(
                        remote_server.name if remote_server is not None else t(language, "none")
                    ),
                    schedule=self._schedule_label(
                        language,
                        ScheduleConfig(
                            profile.schedule_kind,
                            interval=profile.schedule_interval,
                            time=profile.schedule_time,
                            weekday=profile.schedule_weekday,
                            monthday=profile.schedule_monthday,
                        ),
                    ),
                    next_run=(
                        TelegramHtml(
                            f"\n⏭ <b>Next run:</b> "
                            f"{format_user_timestamp(profile.next_scheduled_at, timezone)}"
                        )
                        if profile.next_scheduled_at is not None
                        else TelegramHtml("")
                    ),
                    timezone=timezone_label(profile.updated_at, timezone),
                    source_details=source_details,
                    archive_compression=self._archive_compression_sections(
                        language,
                        CompressionConfig(
                            profile.compression_format,
                            profile.compression_preset,
                            profile.archive_format,
                            profile.compression_algorithm,
                        ),
                    ),
                    encryption=self._encryption_label(
                        language, EncryptionConfig(profile.encryption_mode)
                    ),
                    status=t(language, f"status_{profile.status.value.lower()}"),
                    enabled=t(language, "yes" if profile.enabled else "no"),
                    admins=(
                        TelegramHtml("\n".join(f"• {code(name)}" for name in names))
                        if names
                        else t(language, "none")
                    ),
                ),
                buttons=inline,
            )
        await self.transport.send_text(
            user.telegram_user_id, t(language, "backups"), buttons=backups_menu(language)
        )

    async def _select_backup(
        self, session: AsyncSession, user: User, state: State, prompt: str
    ) -> None:
        await self._selection(
            user, state, await BackupRepository(session).list_active(), prompt, "📦"
        )

    async def _begin_backup_edit(self, user: User, target_id: int) -> None:
        await self.fsm.set(user.id, State.EDIT_BACKUP_ACTION, {"target_id": target_id})
        await self._send(user, "edit_backup_prompt", buttons=edit_backup_menu(user.language_code))

    async def _begin_backup_reconfigure(
        self, session: AsyncSession, user: User, target_id: int
    ) -> None:
        profile = await BackupRepository(session).by_id(target_id)
        if profile is None or profile.archived_at is not None:
            raise ValueError("backup profile not found")
        await session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
        data: dict[str, Any] = {
            "editing_profile_id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "enabled": profile.enabled,
            "original_source_type": profile.source_type,
            "original_encryption_mode": profile.encryption_mode,
            "existing_age_recipients": tuple(json.loads(profile.age_recipients or "[]")),
            "can_preserve_age_passphrase": profile.encrypted_age_passphrase is not None,
        }
        if profile.database_source is not None:
            source = profile.database_source
            data.update(
                {
                    "existing_database_engine": source.engine,
                    "existing_database_host": source.host,
                    "existing_database_port": source.port,
                    "existing_database_name": source.database_name,
                    "existing_database_username": source.username,
                    "existing_tls_mode": source.tls_mode,
                    "existing_tls_ca_certificate": source.tls_ca_certificate,
                    "existing_mssql_in_docker": source.mssql_in_docker,
                    "existing_mssql_backup_path": source.mssql_backup_path,
                    "existing_mssql_host_backup_path": source.mssql_host_backup_path,
                    "can_preserve_database_password": bool(source.encrypted_password),
                }
            )
        if profile.files_source is not None:
            await session.refresh(profile.files_source, ("paths",))
            data.update(
                {
                    "existing_files_location": profile.files_source.location,
                    "existing_files_paths": tuple(path.path for path in profile.files_source.paths),
                }
            )
        if profile.gitlab_source is not None:
            data.update(
                {
                    "existing_gitlab_location": profile.gitlab_source.location,
                    "existing_gitlab_remote_server_id": profile.gitlab_source.remote_server_id,
                    "existing_gitlab_container": profile.gitlab_source.container_name,
                    "existing_gitlab_backup_path": profile.gitlab_source.backup_path,
                }
            )
        item = await self.fsm.set(user.id, State.ADD_BACKUP_NAME, data)
        await self._send(
            user,
            "ask_edit_backup_name",
            current=item.data["name"],
            buttons=cancel_back(user.language_code),
        )
        await self._send(user, "edit_keep_hint")

    async def _begin_backup_delete(self, session: AsyncSession, user: User, target_id: int) -> None:
        profile = await BackupRepository(session).by_id(target_id)
        if profile is None or profile.archived_at is not None:
            return
        await self.fsm.set(user.id, State.CONFIRM_BACKUP_DELETE, {"target_id": target_id})
        await self._send(
            user,
            "backup_delete_summary",
            name=profile.name,
            source=self._source_label(user.language_code, profile.source_type),
            status=t(user.language_code, f"status_{profile.status.value.lower()}"),
            buttons=confirmation(user.language_code, deleting=True),
        )

    async def _begin_backup_access(self, session: AsyncSession, user: User, target_id: int) -> None:
        item = await self.fsm.set(user.id, State.BACKUP_ADMIN_ACCESS, {"target_id": target_id})
        await self._prepare_backup_access(session, item, target_id)
        await self._access_keyboard(user, item, admin_direction=False)
