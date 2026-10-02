import asyncio
import secrets
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import Button

from application.admin_service import AdminService
from application.backup_service import BackupService
from application.profile_config import (
    CompressionConfig,
    DatabaseConfig,
    EncryptionConfig,
    FilesConfig,
    GitLabConfig,
    LocalSourcePathMapper,
    ScheduleConfig,
    validate_docker_container_name,
)
from application.remote_server_service import RemoteServerService
from application.settings_service import SettingsService
from application.validation import validate_name
from application.worker_paths import available_worker_paths
from database.models import User
from domain.enums import (
    CompressionAlgorithm,
    DatabaseEngine,
    EncryptionMode,
    ScheduleKind,
    SourceLocation,
    SourceType,
    TLSMode,
)
from infrastructure.local_source_validation import LocalValidationUnavailable, validate_local_source
from infrastructure.repositories import (
    AccessRepository,
    ConflictError,
    RemoteServerRepository,
    UserRepository,
)
from presentation.telegram.archive_compression import (
    ARCHIVE_ACTIONS,
    NEW_PRESET_ACTIONS,
    TAR_COMPRESSION_ACTIONS,
)
from presentation.telegram.constants import (
    AGE_UNLOCK,
    DATABASE_CREDENTIAL,
    ENGINE_ACTIONS,
    SCHEDULE_ACTIONS,
    SOURCE_ACTIONS,
    SSH_IDENTITY,
    SSH_UNLOCK,
    TLS_ACTIONS,
)
from presentation.telegram.datetime_format import timezone_label
from presentation.telegram.formatting import TelegramHtml, code
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    admins_menu,
    archive_types,
    backups_menu,
    cancel_back,
    compression_presets,
    compression_types,
    confirmation,
    database_engines,
    edit_admin_menu,
    edit_backup_menu,
    edit_server_menu,
    encryption_types,
    language_menu,
    locations,
    remote_servers_menu,
    reply,
    rows,
    schedule_modes,
    schedule_types,
    source_types,
    tls_modes,
    yes_no,
)
from presentation.telegram.language_help import LanguageHelpMixin
from presentation.telegram.profile_summary import file_source_path_lines
from presentation.telegram.runtime import Conversation, State

log = structlog.get_logger()


class WizardMixin(LanguageHelpMixin):
    async def _flow(
        self,
        session: AsyncSession,
        user: User,
        text: str,
        action: str | None,
        item: Conversation,
    ) -> None:
        state, language = item.state, user.language_code
        log.info(
            "telegram_wizard_input",
            user_id=user.id,
            callback_data=action,
            current_state=state.value,
            expected_state=state.value,
            validation_result="pending",
        )
        try:
            if action in {"previous_page", "next_page"}:
                await self._paginate(user, item, action)
                return
            if state is State.EDIT_BACKUP_TIMEZONE:
                await SettingsService(session).set_backup_timezone(user, text)
                await self.fsm.clear(user.id)
                await self._settings(session, user)
            elif state is State.ADD_SERVER_NAME:
                item.data["name"] = validate_name(text)
                await self.fsm.transition(user.id, state, State.ADD_SERVER_HOST)
                await self._send(user, "ask_server_host", buttons=cancel_back(language))
            elif state is State.ADD_SERVER_HOST:
                item.data["host"] = text
                await self.fsm.transition(user.id, state, State.ADD_SERVER_PORT)
                await self._send(user, "ask_server_port", buttons=cancel_back(language))
            elif state is State.ADD_SERVER_PORT:
                item.data["port"] = self._port(text)
                await self.fsm.transition(user.id, state, State.ADD_SERVER_USERNAME)
                await self._send(user, "ask_server_username", buttons=cancel_back(language))
            elif state is State.ADD_SERVER_USERNAME:
                item.data["username"] = text
                await self.fsm.transition(user.id, state, State.ADD_SERVER_PRIVATE_KEY)
                await self._send(user, "ask_server_private_key", buttons=cancel_back(language))
            elif state is State.ADD_SERVER_PRIVATE_KEY:
                await self.fsm.secrets.put(user.id, SSH_IDENTITY, text)
                await self.fsm.transition(user.id, state, State.ADD_SERVER_PASSPHRASE)
                await self._send(user, "ask_server_passphrase", buttons=cancel_back(language))
            elif state is State.ADD_SERVER_PASSPHRASE:
                if self.cipher is None:
                    raise ValueError("secret cipher unavailable")
                if (
                    text == "-"
                    and "pending" in item.data
                    and not await self.fsm.secrets.contains(user.id, SSH_IDENTITY)
                ):
                    pending = item.data["pending"]
                    await self.fsm.transition(user.id, state, State.CONFIRM_SERVER_FINGERPRINT)
                    await self._send(
                        user,
                        "server_fingerprint_confirm",
                        fingerprint=pending.fingerprint,
                        buttons=confirmation(language),
                    )
                    return
                if text != "-":
                    await self.fsm.secrets.put(user.id, SSH_UNLOCK, text)
                try:
                    private_key = await self.fsm.secrets.take(user.id, SSH_IDENTITY)
                    passphrase = (
                        await self.fsm.secrets.take(user.id, SSH_UNLOCK) if text != "-" else None
                    )
                    pending = await RemoteServerService(session, self.cipher).probe(
                        user,
                        name=str(item.data["name"]),
                        host=str(item.data["host"]),
                        port=int(item.data["port"]),
                        username=str(item.data["username"]),
                        private_key=private_key,
                        passphrase=passphrase,
                    )
                finally:
                    await self.fsm.secrets.clear(user.id)
                item.data["pending"] = pending
                await self.fsm.transition(user.id, state, State.CONFIRM_SERVER_FINGERPRINT)
                await self._send(
                    user,
                    "server_fingerprint_confirm",
                    fingerprint=pending.fingerprint,
                    buttons=confirmation(language),
                )
            elif state is State.CONFIRM_SERVER_FINGERPRINT:
                if action != "confirm" or self.cipher is None:
                    raise ValueError("confirmation required")
                service = RemoteServerService(session, self.cipher)
                if "target_id" in item.data:
                    await service.update(user, int(item.data["target_id"]), item.data["pending"])
                    message = "server_updated"
                else:
                    await service.confirm(user, item.data["pending"])
                    message = "server_saved"
                await self.fsm.clear(user.id)
                await self._send(user, message, buttons=remote_servers_menu(language))
                await self._show_servers(session, user)
            elif state is State.SELECT_SERVER_EDIT:
                await self._begin_server_edit(user, self._selected_id(item, text))
            elif state is State.EDIT_SERVER_ACTION:
                target_id = int(item.data["target_id"])
                if action == "reconfigure_server":
                    await self._begin_server_reconfigure(session, user, target_id)
                elif action == "toggle_enabled":
                    if self.cipher is None:
                        raise ValueError("secret cipher unavailable")
                    server = await RemoteServerRepository(session).active_by_id(target_id)
                    await RemoteServerService(session, self.cipher).set_enabled(
                        user, target_id, not server.enabled
                    )
                    await self.fsm.clear(user.id)
                    await self._send(
                        user,
                        "server_status_changed",
                        buttons=remote_servers_menu(language),
                    )
                    await self._show_servers(session, user)
                else:
                    raise ValueError("invalid server edit action")
            elif state is State.SELECT_SERVER_DELETE:
                await self._begin_server_delete(session, user, self._selected_id(item, text))
            elif state is State.CONFIRM_SERVER_DELETE:
                if action != "confirm_delete" or self.cipher is None:
                    raise ValueError("confirmation required")
                await RemoteServerService(session, self.cipher).delete(
                    user, int(item.data["target_id"])
                )
                await self.fsm.clear(user.id)
                await self._send(user, "server_deleted", buttons=remote_servers_menu(language))
                await self._show_servers(session, user)
            elif state is State.ADD_BACKUP_NAME:
                if text != "-" or "editing_profile_id" not in item.data:
                    item.data["name"] = validate_name(text)
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DESCRIPTION)
                await self._send(
                    user,
                    "ask_edit_description"
                    if "editing_profile_id" in item.data
                    else "ask_description",
                    buttons=cancel_back(language),
                )
            elif state is State.ADD_BACKUP_DESCRIPTION:
                if len(text) > 2000:
                    raise ValueError("too long")
                if "editing_profile_id" in item.data:
                    if text != "-":
                        item.data["description"] = None if text == "!" else text
                else:
                    item.data["description"] = None if text == "-" else text
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_SOURCE)
                await self._send(user, "choose_source", buttons=source_types(language))
            elif state is State.ADD_BACKUP_SOURCE:
                source = SOURCE_ACTIONS.get(action or "")
                if source is None:
                    raise ValueError("invalid source")
                item.data["source_type"] = source
                if source is SourceType.DATABASE:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_ENGINE)
                    await self._send(
                        user, "choose_database_engine", buttons=database_engines(language)
                    )
                elif source is SourceType.FILES_DIRECTORIES:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_FILES_LOCATION)
                    await self._send(user, "choose_location", buttons=locations(language))
                else:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_GITLAB_LOCATION)
                    await self._send(user, "choose_gitlab_location", buttons=locations(language))
            elif state is State.ADD_BACKUP_DATABASE_ENGINE:
                engine = ENGINE_ACTIONS.get(action or "")
                if engine is None:
                    raise ValueError("invalid database engine")
                item.data["engine"] = engine
                item.data["preservable_database"] = (
                    item.data.get("original_source_type") is SourceType.DATABASE
                    and item.data.get("existing_database_engine") is engine
                )
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_HOST)
                await self._send(user, "ask_database_host", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_DATABASE_HOST:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data["existing_database_host"])
                item.data["database_host"] = text
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_PORT)
                await self._send(user, "ask_database_port", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_DATABASE_PORT:
                item.data["database_port"] = (
                    int(item.data["existing_database_port"])
                    if text == "-" and item.data.get("preservable_database")
                    else self._port(text)
                )
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_NAME)
                await self._send(user, "ask_database_name", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_DATABASE_NAME:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data["existing_database_name"])
                item.data["database_name"] = validate_name(text, "database name")
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_USERNAME)
                await self._send(user, "ask_database_username", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_DATABASE_USERNAME:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data["existing_database_username"])
                item.data["database_username"] = validate_name(text, "database username")
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_PASSWORD)
                await self._send(
                    user,
                    "ask_edit_database_password"
                    if item.data.get("preservable_database")
                    else "ask_database_password",
                    buttons=cancel_back(language),
                )
            elif state is State.ADD_BACKUP_DATABASE_PASSWORD:
                if not text:
                    raise ValueError("password required")
                if text == "-" and item.data.get("preservable_database"):
                    if not item.data.get("can_preserve_database_password"):
                        raise ValueError("no password to preserve")
                    item.data["preserve_database_password"] = True
                else:
                    await self.fsm.secrets.put(user.id, DATABASE_CREDENTIAL, text)
                    item.data["preserve_database_password"] = False
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_TLS)
                await self._send(user, "choose_tls", buttons=tls_modes(language))
            elif state is State.ADD_BACKUP_DATABASE_TLS:
                tls_mode = TLS_ACTIONS.get(action or "")
                if tls_mode is None:
                    raise ValueError("invalid TLS mode")
                item.data["tls_mode"] = tls_mode
                if tls_mode in {TLSMode.VERIFY_CA, TLSMode.VERIFY_FULL}:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_DATABASE_TLS_CA)
                    await self._send(user, "ask_tls_ca", buttons=cancel_back(language))
                else:
                    await self._after_database_tls(session, user, item, state)
            elif state is State.ADD_BACKUP_DATABASE_TLS_CA:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data.get("existing_tls_ca_certificate") or "")
                if "BEGIN CERTIFICATE" not in text or "END CERTIFICATE" not in text:
                    raise ValueError("invalid CA certificate")
                item.data["tls_ca_certificate"] = text
                await self._after_database_tls(session, user, item, state)
            elif state is State.ADD_BACKUP_MSSQL_DOCKER:
                if action not in {"yes", "no"}:
                    raise ValueError("yes or no required")
                item.data["mssql_in_docker"] = action == "yes"
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_MSSQL_PATH)
                await self._send(user, "ask_mssql_backup_path", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_MSSQL_PATH:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data.get("existing_mssql_backup_path") or "")
                item.data["mssql_backup_path"] = text
                if item.data.get("mssql_in_docker"):
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_MSSQL_HOST_PATH)
                    await self._send(user, "ask_mssql_host_path", buttons=cancel_back(language))
                else:
                    await self._advance_mssql_server(session, user, item, state)
            elif state is State.ADD_BACKUP_MSSQL_HOST_PATH:
                if text == "-" and item.data.get("preservable_database"):
                    text = str(item.data.get("existing_mssql_host_backup_path") or "")
                item.data["mssql_host_backup_path"] = text
                await self._advance_mssql_server(session, user, item, state)
            elif state is State.ADD_BACKUP_MSSQL_SERVER:
                if action != "none":
                    server_id = self._selected_id(item, text)
                    server = await RemoteServerRepository(session).active_by_id(server_id)
                    item.data["artifact_remote_server_id"] = server_id
                    item.data["remote_server_name"] = server.name
                item.option_ids.clear()
                await self._advance_to_schedule(session, user, item, state)
            elif state is State.ADD_BACKUP_FILES_LOCATION:
                if action not in {"location_local", "location_remote"}:
                    raise ValueError("invalid location")
                item.data["location"] = (
                    SourceLocation.LOCAL
                    if action == "location_local"
                    else SourceLocation.REMOTE_SERVER
                )
                if item.data["location"] is SourceLocation.LOCAL:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_FILES_PATHS)
                    await self._path_prompt(user, item)
                else:
                    servers = await RemoteServerRepository(session).list_active(enabled_only=True)
                    if not servers:
                        raise ValueError("no enabled remote server")
                    item.option_ids = {
                        f"🖥 {server.name} · {index}": server.id
                        for index, server in enumerate(servers, 1)
                    }
                    item.data.update(
                        {"page": 0, "view": "selection", "prompt": "choose_remote_server"}
                    )
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_FILES_SERVER)
                    await self._render_selection(user, item)
            elif state is State.ADD_BACKUP_FILES_SERVER:
                server_id = self._selected_id(item, text)
                server = await RemoteServerRepository(session).active_by_id(server_id)
                item.data["remote_server_id"] = server_id
                item.data["remote_server_name"] = server.name
                item.option_ids.clear()
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_FILES_PATHS)
                await self._path_prompt(user, item)
            elif state is State.ADD_BACKUP_FILES_PATHS:
                if (
                    text == "-"
                    and item.data.get("original_source_type") is SourceType.FILES_DIRECTORIES
                ):
                    item.data["paths"] = item.data["existing_files_paths"]
                else:
                    item.data["paths"] = tuple(
                        line.strip() for line in text.splitlines() if line.strip()
                    )
                FilesConfig(
                    item.data["location"], item.data["paths"], item.data.get("remote_server_id")
                ).validate()
                if item.data["location"] is SourceLocation.LOCAL:
                    mapper = LocalSourcePathMapper(
                        self.settings.allowed_source_roots(),
                        self.settings.denied_source_roots(),
                        self.settings.local_source_mounts(),
                    )
                    # Existing profiles persist host paths. Only the edit keep-value shortcut
                    # translates them; newly entered paths must be worker-visible.
                    paths = item.data["paths"]
                    if text == "-":
                        paths = tuple(mapper.container_path(path) for path in paths)
                    validated = []
                    for path in paths:
                        validated.append(await validate_local_source(path, self.settings))
                    item.data["paths"] = tuple(validated)
                await self._advance_to_schedule(session, user, item, state)
            elif state is State.ADD_BACKUP_GITLAB_LOCATION:
                if action not in {"location_local", "location_remote"}:
                    raise ValueError("invalid location")
                item.data["gitlab_location"] = (
                    SourceLocation.LOCAL
                    if action == "location_local"
                    else SourceLocation.REMOTE_SERVER
                )
                item.data.pop("gitlab_remote_server_id", None)
                item.data.pop("remote_server_name", None)
                item.data.pop("gitlab_backup_path", None)
                if item.data["gitlab_location"] is SourceLocation.LOCAL:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_GITLAB_CONTAINER)
                    await self._send(user, "ask_gitlab_container", buttons=cancel_back(language))
                else:
                    servers = await RemoteServerRepository(session).list_active(enabled_only=True)
                    if not servers:
                        await self._send(user, "no_remote_servers_available")
                        await self._send(
                            user, "choose_gitlab_location", buttons=locations(language)
                        )
                        return
                    item.option_ids = {
                        f"🖥 {server.name} · {index}": server.id
                        for index, server in enumerate(servers, 1)
                    }
                    item.data.update(
                        {"page": 0, "view": "selection", "prompt": "choose_remote_server"}
                    )
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_GITLAB_SERVER)
                    await self._render_selection(user, item)
            elif state is State.ADD_BACKUP_GITLAB_SERVER:
                server_id = self._selected_id(item, text)
                server = await RemoteServerRepository(session).active_by_id(server_id)
                item.data["gitlab_remote_server_id"] = server_id
                item.data["remote_server_name"] = server.name
                item.option_ids.clear()
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_GITLAB_CONTAINER)
                await self._send(user, "ask_gitlab_container", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_GITLAB_CONTAINER:
                if text == "-" and item.data.get("original_source_type") is SourceType.GITLAB:
                    text = str(item.data["existing_gitlab_container"])
                item.data["gitlab_container"] = validate_docker_container_name(text)
                if item.data["gitlab_location"] is SourceLocation.LOCAL:
                    GitLabConfig(
                        item.data["gitlab_location"],
                        item.data["gitlab_container"],
                    ).validate()
                    await self._gitlab_path_information(user)
                    await self._send(user, "gitlab_exclusive_warning")
                    await self._advance_to_schedule(session, user, item, state)
                else:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_GITLAB_REMOTE_PATH)
                    await self._send(user, "ask_gitlab_remote_path", buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_GITLAB_REMOTE_PATH:
                if (
                    text == "-"
                    and item.data.get("original_source_type") is SourceType.GITLAB
                    and item.data.get("existing_gitlab_location") is SourceLocation.REMOTE_SERVER
                ):
                    text = str(item.data["existing_gitlab_backup_path"])
                item.data["gitlab_backup_path"] = text.strip()
                GitLabConfig(
                    item.data["gitlab_location"],
                    item.data["gitlab_container"],
                    item.data["gitlab_backup_path"],
                    item.data.get("gitlab_remote_server_id"),
                ).validate()
                await self._advance_to_schedule(session, user, item, state)
            elif state is State.ADD_BACKUP_SCHEDULE:
                if action == "schedule_interval_mode":
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_INTERVAL)
                    await self._render_wizard_step(session, user, item)
                elif action == "schedule_cron":
                    item.data["schedule_kind"] = ScheduleKind.CRON
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_SCHEDULE_VALUE)
                    await self._render_wizard_step(session, user, item)
                else:
                    raise ValueError("invalid schedule mode")
            elif state is State.ADD_BACKUP_INTERVAL:
                schedule_kind = SCHEDULE_ACTIONS.get(action or "")
                if schedule_kind is None or schedule_kind is ScheduleKind.CRON:
                    raise ValueError("invalid schedule")
                item.data["schedule_kind"] = schedule_kind
                if schedule_kind in {ScheduleKind.MANUAL, ScheduleKind.ONCE}:
                    item.data["schedule"] = ScheduleConfig(schedule_kind)
                    await self._advance_to_archive(user, item, state)
                else:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_SCHEDULE_VALUE)
                    prompt = "ask_schedule_interval"
                    if schedule_kind is ScheduleKind.DAILY:
                        prompt = "ask_schedule_daily"
                    elif schedule_kind is ScheduleKind.WEEKLY:
                        prompt = "ask_schedule_weekly"
                    elif schedule_kind is ScheduleKind.MONTHLY:
                        prompt = "ask_schedule_monthly"
                    await self._send(user, prompt, buttons=cancel_back(language))
            elif state is State.ADD_BACKUP_SCHEDULE_VALUE:
                item.data["schedule"] = self._schedule_value(item.data["schedule_kind"], text)
                await self._advance_to_archive(user, item, state)
            elif state is State.ADD_BACKUP_ARCHIVE:
                archive_format = ARCHIVE_ACTIONS.get(action or "")
                if archive_format is None:
                    raise ValueError("invalid archive format")
                item.data["archive_format"] = archive_format
                await self._advance_to_compression(user, item, state)
            elif state is State.ADD_BACKUP_COMPRESSION:
                algorithm = TAR_COMPRESSION_ACTIONS.get(action or "")
                if algorithm is None:
                    raise ValueError("invalid compression")
                item.data["compression_algorithm"] = algorithm
                if algorithm is CompressionAlgorithm.NONE:
                    item.data["compression"] = CompressionConfig.separated(
                        item.data["archive_format"], algorithm, None
                    )
                    await self._advance_to_encryption(user, item, state)
                    return
                await self.fsm.transition(user.id, state, State.ADD_BACKUP_COMPRESSION_PRESET)
                await self._send(
                    user, "choose_compression_preset", buttons=compression_presets(language)
                )
            elif state is State.ADD_BACKUP_COMPRESSION_PRESET:
                preset = NEW_PRESET_ACTIONS.get(action or "")
                if preset is None:
                    raise ValueError("invalid compression preset")
                item.data["compression"] = CompressionConfig.separated(
                    item.data["archive_format"], item.data["compression_algorithm"], preset
                )
                await self._advance_to_encryption(user, item, state)
            elif state is State.ADD_BACKUP_ENCRYPTION:
                modes = {
                    "encryption_none": EncryptionMode.NONE,
                    "encryption_age_public": EncryptionMode.AGE_PUBLIC_KEY,
                    "encryption_age_passphrase": EncryptionMode.AGE_PASSPHRASE,
                }
                mode = modes.get(action or "")
                if mode is None:
                    raise ValueError("invalid encryption")
                item.data["encryption_mode"] = mode
                if mode is EncryptionMode.NONE:
                    item.data["encryption"] = EncryptionConfig(mode)
                    await self._prepare_new_backup_admins(session, user, item, state)
                else:
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_ENCRYPTION_SECRET)
                    await self._send(
                        user,
                        "ask_age_recipients"
                        if mode is EncryptionMode.AGE_PUBLIC_KEY
                        else (
                            "ask_edit_age_passphrase"
                            if item.data.get("original_encryption_mode")
                            is EncryptionMode.AGE_PASSPHRASE
                            else "ask_age_passphrase"
                        ),
                        buttons=cancel_back(language),
                    )
            elif state is State.ADD_BACKUP_ENCRYPTION_SECRET:
                mode = item.data["encryption_mode"]
                if mode is EncryptionMode.AGE_PUBLIC_KEY:
                    if (
                        text == "-"
                        and item.data.get("original_encryption_mode")
                        is EncryptionMode.AGE_PUBLIC_KEY
                    ):
                        recipients = item.data["existing_age_recipients"]
                    else:
                        recipients = tuple(
                            line.strip() for line in text.splitlines() if line.strip()
                        )
                    item.data["encryption"] = EncryptionConfig(mode, recipients)
                    item.data["encryption"].validate()
                else:
                    if not text:
                        raise ValueError("passphrase required")
                    if (
                        text == "-"
                        and item.data.get("original_encryption_mode")
                        is EncryptionMode.AGE_PASSPHRASE
                        and item.data.get("can_preserve_age_passphrase")
                    ):
                        item.data["preserve_age_passphrase"] = True
                    else:
                        await self.fsm.secrets.put(user.id, AGE_UNLOCK, text)
                        item.data["preserve_age_passphrase"] = False
                    item.data["encryption"] = EncryptionConfig(mode)
                await self._prepare_new_backup_admins(session, user, item, state)
            elif state is State.ADD_BACKUP_ADMINS:
                selected: set[int] = item.data["selected"]
                if action in {"save_access", "remove_all"}:
                    if action == "remove_all":
                        selected.clear()
                    await self.fsm.transition(user.id, state, State.ADD_BACKUP_CONFIRM)
                    await self._show_profile_summary(session, user, item)
                else:
                    unchecked = item.option_ids.get(text.replace("✅ ", "⬜ ", 1))
                    if unchecked is None:
                        raise ValueError("unknown admin")
                    selected.symmetric_difference_update({unchecked})
                    await self._access_keyboard(user, item, admin_direction=False)
            elif state is State.ADD_BACKUP_CONFIRM:
                await self._confirm_backup_profile(session, user, item, action)
                return
            elif state is State.ADD_ADMIN_NAME:
                item.data["name"] = validate_name(text, "display name")
                await self.fsm.transition(user.id, state, State.ADD_ADMIN_ID)
                await self._send(user, "ask_telegram_id", buttons=cancel_back(language))
            elif state is State.ADD_ADMIN_ID:
                item.data["telegram_id"] = self._numeric_id(text)
                await self.fsm.transition(user.id, state, State.ADD_ADMIN_LANGUAGE)
                await self._send(user, "ask_admin_language", buttons=language_menu(language))
            elif state is State.ADD_ADMIN_LANGUAGE:
                chosen = self._language_action(action)
                item.data["language"] = chosen
                await self.fsm.transition(user.id, state, State.ADD_ADMIN_CONFIRM)
                await self._send(
                    user,
                    "admin_confirmation",
                    name=item.data["name"],
                    telegram_id=item.data["telegram_id"],
                    admin_language=t(language, f"language_{chosen.value}"),
                    buttons=confirmation(language),
                )
            elif state is State.ADD_ADMIN_CONFIRM:
                if action != "confirm":
                    raise ValueError("confirmation required")
                created = await AdminService(session).create_admin(
                    user,
                    int(item.data["telegram_id"]),
                    str(item.data["name"]),
                    set(),
                    item.data["language"],
                )
                item.data["target_id"] = created.id
                await self._prepare_admin_access(session, item, created.id)
                await self.fsm.transition(user.id, state, State.ADD_ADMIN_ACCESS)
                await self._access_keyboard(user, item, admin_direction=True)
            elif state in {State.ADD_ADMIN_ACCESS, State.EDIT_ADMIN_ACCESS}:
                if await self._handle_access_action(
                    session, user, item, text, action, admin_direction=True
                ):
                    await self._send(user, "admin_saved", buttons=admins_menu(language))
                    await self._show_admins(session, user)
            elif state is State.SELECT_ADMIN_EDIT:
                await self._begin_admin_edit(user, self._selected_id(item, text))
            elif state is State.SELECT_ADMIN_DELETE:
                await self._begin_admin_delete(session, user, self._selected_id(item, text))
            elif state is State.EDIT_ADMIN_ACTION:
                await self._admin_edit_action(session, user, item, action)
            elif state is State.EDIT_ADMIN_NAME:
                await AdminService(session).rename(user, int(item.data["target_id"]), text)
                await self.fsm.clear(user.id)
                await self._send(user, "admin_updated", buttons=admins_menu(language))
                await self._show_admins(session, user)
            elif state is State.EDIT_ADMIN_TELEGRAM_ID:
                await AdminService(session).change_telegram_id(
                    user, int(item.data["target_id"]), self._numeric_id(text)
                )
                await self.fsm.clear(user.id)
                await self._send(user, "admin_updated", buttons=admins_menu(language))
                await self._show_admins(session, user)
            elif state is State.EDIT_ADMIN_LANGUAGE:
                await AdminService(session).change_language(
                    user, self._language_action(action), target_id=int(item.data["target_id"])
                )
                await self.fsm.clear(user.id)
                await self._send(user, "admin_updated", buttons=admins_menu(language))
                await self._show_admins(session, user)
            elif state is State.CONFIRM_ADMIN_DELETE:
                if action != "confirm_delete":
                    raise ValueError("confirmation required")
                await AdminService(session).delete(user, int(item.data["target_id"]))
                await self.fsm.clear(user.id)
                await self._send(user, "admin_deleted", buttons=admins_menu(language))
                await self._show_admins(session, user)
            elif state is State.SELECT_BACKUP_EDIT:
                await self._begin_backup_edit(user, self._selected_id(item, text))
            elif state is State.SELECT_BACKUP_DELETE:
                await self._begin_backup_delete(session, user, self._selected_id(item, text))
            elif state is State.EDIT_BACKUP_ACTION:
                await self._backup_edit_action(session, user, item, action)
            elif state in {State.EDIT_BACKUP_NAME, State.EDIT_BACKUP_DESCRIPTION}:
                if state is State.EDIT_BACKUP_NAME:
                    await BackupService(session).edit(user, int(item.data["target_id"]), name=text)
                else:
                    await BackupService(session).edit(
                        user, int(item.data["target_id"]), description=text
                    )
                await self.fsm.clear(user.id)
                await self._send(user, "backup_updated", buttons=backups_menu(language))
                await self._show_backups(session, user)
            elif state is State.CONFIRM_BACKUP_DELETE:
                if action != "confirm_delete":
                    raise ValueError("confirmation required")
                await BackupService(session).delete(user, int(item.data["target_id"]))
                await self.fsm.clear(user.id)
                await self._send(user, "backup_deleted", buttons=backups_menu(language))
                await self._show_backups(session, user)
            elif state is State.BACKUP_ADMIN_ACCESS:
                if await self._handle_access_action(
                    session, user, item, text, action, admin_direction=False
                ):
                    await self._send(user, "access_saved", buttons=backups_menu(language))
                    await self._show_backups(session, user)
            log.info(
                "telegram_wizard_input",
                user_id=user.id,
                callback_data=action,
                current_state=state.value,
                expected_state=state.value,
                validation_result="accepted",
            )
        except LocalValidationUnavailable:
            await self._send(user, "local_validation_unavailable")
        except (ValueError, KeyError) as exc:
            log.info(
                "telegram_wizard_input",
                user_id=user.id,
                callback_data=action,
                current_state=state.value,
                expected_state=state.value,
                validation_result="rejected",
                reason=type(exc).__name__,
            )
            if await self.fsm.invalid(user.id):
                await self._home(user, "too_many_invalid")
            else:
                await self._send(
                    user,
                    "invalid_cron"
                    if state is State.ADD_BACKUP_SCHEDULE_VALUE
                    and item.data.get("schedule_kind") is ScheduleKind.CRON
                    else "invalid",
                )

    async def _back(self, session: AsyncSession, user: User) -> None:
        current = await self.fsm.get(user.id)
        item = await self.fsm.back(user.id)
        if item is None:
            await self.fsm.clear(user.id)
            if current is not None and current.state in {
                State.ADD_ADMIN_NAME,
                State.SELECT_ADMIN_EDIT,
                State.SELECT_ADMIN_DELETE,
                State.EDIT_ADMIN_ACTION,
                State.CONFIRM_ADMIN_DELETE,
            }:
                await self._show_admins(session, user)
            elif current is not None and current.state in {
                State.ADD_SERVER_NAME,
                State.SELECT_SERVER_EDIT,
                State.SELECT_SERVER_DELETE,
                State.EDIT_SERVER_ACTION,
                State.CONFIRM_SERVER_DELETE,
            }:
                await self._show_servers(session, user)
            elif current is not None and current.state in {
                State.ADD_BACKUP_NAME,
                State.SELECT_BACKUP_EDIT,
                State.SELECT_BACKUP_DELETE,
                State.EDIT_BACKUP_ACTION,
                State.CONFIRM_BACKUP_DELETE,
                State.BACKUP_ADMIN_ACCESS,
            }:
                await self._show_backups(session, user)
            elif current is not None and current.state is State.EDIT_BACKUP_TIMEZONE:
                await self._settings(session, user)
            else:
                await self._home(user, "returned")
            return
        await self._render_wizard_step(session, user, item)

    async def _wizard_prompt(
        self,
        user: User,
        key: str,
        buttons: Any,
        current: Any | None = None,
        **values: Any,
    ) -> None:
        prompt = t(user.language_code, key, **values)
        if current is None:
            await self.transport.send_text(user.telegram_user_id, prompt, buttons=buttons)
            return
        current_label = t(user.language_code, "current_value")
        message = TelegramHtml(f"{prompt}\n\n{current_label}\n{code(current)}")
        await self.transport.send_text(user.telegram_user_id, message, buttons=buttons)

    async def _render_wizard_step(
        self, session: AsyncSession, user: User, item: Conversation
    ) -> None:
        language = user.language_code
        state = item.state
        simple: dict[State, tuple[str, Any, str | None]] = {
            State.ADD_SERVER_NAME: (
                "ask_server_name_edit" if "target_id" in item.data else "ask_server_name",
                cancel_back(language),
                "name",
            ),
            State.ADD_SERVER_HOST: ("ask_server_host", cancel_back(language), "host"),
            State.ADD_SERVER_PORT: ("ask_server_port", cancel_back(language), "port"),
            State.ADD_SERVER_USERNAME: (
                "ask_server_username",
                cancel_back(language),
                "username",
            ),
            State.ADD_BACKUP_NAME: (
                "ask_edit_backup_name" if "editing_profile_id" in item.data else "ask_backup_name",
                cancel_back(language),
                "name",
            ),
            State.ADD_BACKUP_DESCRIPTION: (
                "ask_edit_description" if "editing_profile_id" in item.data else "ask_description",
                cancel_back(language),
                "description",
            ),
            State.ADD_BACKUP_SOURCE: ("choose_source", source_types(language), None),
            State.ADD_BACKUP_DATABASE_ENGINE: (
                "choose_database_engine",
                database_engines(language),
                None,
            ),
            State.ADD_BACKUP_DATABASE_HOST: (
                "ask_database_host",
                cancel_back(language),
                "database_host",
            ),
            State.ADD_BACKUP_DATABASE_PORT: (
                "ask_database_port",
                cancel_back(language),
                "database_port",
            ),
            State.ADD_BACKUP_DATABASE_NAME: (
                "ask_database_name",
                cancel_back(language),
                "database_name",
            ),
            State.ADD_BACKUP_DATABASE_USERNAME: (
                "ask_database_username",
                cancel_back(language),
                "database_username",
            ),
            State.ADD_BACKUP_DATABASE_TLS: ("choose_tls", tls_modes(language), None),
            State.ADD_BACKUP_DATABASE_TLS_CA: (
                "ask_tls_ca",
                cancel_back(language),
                None,
            ),
            State.ADD_BACKUP_MSSQL_DOCKER: (
                "ask_mssql_docker",
                yes_no(language),
                None,
            ),
            State.ADD_BACKUP_MSSQL_PATH: (
                "ask_mssql_backup_path",
                cancel_back(language),
                "mssql_backup_path",
            ),
            State.ADD_BACKUP_MSSQL_HOST_PATH: (
                "ask_mssql_host_path",
                cancel_back(language),
                "mssql_host_backup_path",
            ),
            State.ADD_BACKUP_FILES_LOCATION: (
                "choose_location",
                locations(language),
                None,
            ),
            State.ADD_BACKUP_FILES_PATHS: ("ask_paths", cancel_back(language), "paths"),
            State.ADD_BACKUP_GITLAB_LOCATION: (
                "choose_gitlab_location",
                locations(language),
                None,
            ),
            State.ADD_BACKUP_GITLAB_CONTAINER: (
                "ask_gitlab_container",
                cancel_back(language),
                "gitlab_container",
            ),
            State.ADD_BACKUP_GITLAB_REMOTE_PATH: (
                "ask_gitlab_remote_path",
                cancel_back(language),
                "gitlab_backup_path",
            ),
            State.ADD_BACKUP_ARCHIVE: (
                "archive_explanation",
                archive_types(language),
                None,
            ),
            State.ADD_BACKUP_COMPRESSION: (
                "compression_explanation",
                compression_types(language),
                None,
            ),
            State.ADD_BACKUP_COMPRESSION_PRESET: (
                "choose_compression_preset",
                compression_presets(language),
                None,
            ),
            State.ADD_BACKUP_ENCRYPTION: (
                "choose_encryption",
                encryption_types(language),
                None,
            ),
            State.ADD_ADMIN_NAME: ("ask_admin_name", cancel_back(language), "name"),
            State.ADD_ADMIN_ID: ("ask_telegram_id", cancel_back(language), "telegram_id"),
            State.EDIT_ADMIN_NAME: ("ask_new_name", cancel_back(language), None),
            State.EDIT_ADMIN_TELEGRAM_ID: (
                "ask_new_telegram_id",
                cancel_back(language),
                None,
            ),
            State.EDIT_BACKUP_NAME: ("ask_new_name", cancel_back(language), None),
            State.EDIT_BACKUP_DESCRIPTION: (
                "ask_description",
                cancel_back(language),
                None,
            ),
        }
        if state is State.ADD_BACKUP_ADMINS:
            await self._access_keyboard(user, item, admin_direction=False)
        elif state is State.ADD_BACKUP_CONFIRM:
            await self._show_profile_summary(session, user, item)
        elif state is State.ADD_BACKUP_FILES_PATHS:
            await self._path_prompt(user, item)
        elif state is State.ADD_BACKUP_SCHEDULE:
            await self._schedule_mode_prompt(session, user, item)
        elif state is State.ADD_BACKUP_INTERVAL:
            settings = await SettingsService(session).get()
            await self._send(
                user,
                "choose_schedule",
                timezone=settings.backup_timezone,
                buttons=schedule_types(language),
            )
        elif state is State.ADD_BACKUP_SCHEDULE_VALUE:
            prompts = {
                ScheduleKind.DAILY: "ask_schedule_daily",
                ScheduleKind.WEEKLY: "ask_schedule_weekly",
                ScheduleKind.MONTHLY: "ask_schedule_monthly",
                ScheduleKind.CRON: "ask_schedule_cron",
            }
            schedule_kind = item.data.get("schedule_kind")
            prompt = (
                prompts[schedule_kind]
                if isinstance(schedule_kind, ScheduleKind) and schedule_kind in prompts
                else "ask_schedule_interval"
            )
            if schedule_kind is ScheduleKind.CRON:
                stored = await SettingsService(session).get()
                current = item.data.get("schedule") or item.data.get("existing_schedule")
                await self._wizard_prompt(
                    user,
                    prompt,
                    cancel_back(language),
                    current=current.cron_expression if current is not None else None,
                    timezone=stored.backup_timezone,
                )
            else:
                await self._send(user, prompt, buttons=cancel_back(language))
        elif state in {
            State.ADD_BACKUP_FILES_SERVER,
            State.ADD_BACKUP_MSSQL_SERVER,
            State.ADD_BACKUP_GITLAB_SERVER,
        }:
            if not item.option_ids:
                servers = await RemoteServerRepository(session).list_active(enabled_only=True)
                item.option_ids = {
                    f"🖥 {server.name} · {index}": server.id
                    for index, server in enumerate(servers, 1)
                }
                item.data.update({"view": "selection", "prompt": "choose_remote_server"})
            await self._render_selection(user, item)
        elif state in {State.ADD_SERVER_PRIVATE_KEY, State.ADD_SERVER_PASSPHRASE}:
            await self._send(
                user,
                "existing_credential"
                if await self.fsm.secrets.contains(user.id) or "pending" in item.data
                else (
                    "ask_server_private_key"
                    if state is State.ADD_SERVER_PRIVATE_KEY
                    else "ask_server_passphrase"
                ),
                buttons=cancel_back(language),
            )
        elif state is State.ADD_BACKUP_DATABASE_PASSWORD:
            await self._send(
                user,
                "existing_credential"
                if await self.fsm.secrets.contains(user.id, DATABASE_CREDENTIAL)
                else (
                    "ask_edit_database_password"
                    if item.data.get("preservable_database")
                    else "ask_database_password"
                ),
                buttons=cancel_back(language),
            )
        elif state is State.ADD_BACKUP_ENCRYPTION_SECRET:
            public_key = item.data.get("encryption_mode") is EncryptionMode.AGE_PUBLIC_KEY
            await self._send(
                user,
                "ask_age_recipients"
                if public_key
                else (
                    "existing_credential"
                    if await self.fsm.secrets.contains(user.id, AGE_UNLOCK)
                    else (
                        "ask_edit_age_passphrase"
                        if item.data.get("can_preserve_age_passphrase")
                        else "ask_age_passphrase"
                    )
                ),
                buttons=cancel_back(language),
            )
        elif state in {State.ADD_ADMIN_LANGUAGE, State.EDIT_ADMIN_LANGUAGE}:
            await self._send(
                user,
                "ask_admin_language" if state is State.ADD_ADMIN_LANGUAGE else "language_prompt",
                buttons=language_menu(language),
            )
        elif state is State.ADD_ADMIN_CONFIRM:
            chosen = item.data["language"]
            await self._send(
                user,
                "admin_confirmation",
                name=item.data["name"],
                telegram_id=item.data["telegram_id"],
                admin_language=t(language, f"language_{chosen.value}"),
                buttons=confirmation(language),
            )
        elif state is State.CONFIRM_SERVER_FINGERPRINT:
            await self._send(
                user,
                "server_fingerprint_confirm",
                fingerprint=item.data["pending"].fingerprint,
                buttons=confirmation(language),
            )
        elif state is State.EDIT_ADMIN_ACTION:
            await self._send(user, "edit_admin_prompt", buttons=edit_admin_menu(language))
        elif state is State.EDIT_SERVER_ACTION:
            await self._send(user, "edit_server_prompt", buttons=edit_server_menu(language))
        elif state is State.EDIT_BACKUP_ACTION:
            await self._send(user, "edit_backup_prompt", buttons=edit_backup_menu(language))
        elif state in {State.ADD_ADMIN_ACCESS, State.EDIT_ADMIN_ACCESS}:
            await self._access_keyboard(user, item, admin_direction=True)
        elif state is State.BACKUP_ADMIN_ACCESS:
            await self._access_keyboard(user, item, admin_direction=False)
        elif item.data.get("view") == "selection":
            await self._render_selection(user, item)
        elif state in simple:
            prompt, buttons, field = simple[state]
            values = {"current": item.data["name"]} if prompt == "ask_edit_backup_name" else {}
            current_value = item.data.get(field) if field is not None else None
            if isinstance(current_value, (tuple, list)):
                current_value = "\n".join(str(value) for value in current_value)
            await self._wizard_prompt(
                user,
                prompt,
                buttons,
                current=current_value,
                **values,
            )
        else:
            raise ValueError(f"wizard step renderer is missing for {state.value}")

    async def _after_database_tls(
        self, session: AsyncSession, user: User, item: Conversation, state: State
    ) -> None:
        if item.data["engine"] is DatabaseEngine.MSSQL:
            await self.fsm.transition(user.id, state, State.ADD_BACKUP_MSSQL_DOCKER)
            await self._send(user, "ask_mssql_docker", buttons=yes_no(user.language_code))
        else:
            await self._advance_to_schedule(session, user, item, state)

    async def _advance_to_schedule(
        self, session: AsyncSession, user: User, item: Conversation, state: State
    ) -> None:
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_SCHEDULE)
        await self._schedule_mode_prompt(session, user, item)

    async def _gitlab_path_information(self, user: User) -> None:
        if self.settings.GITLAB_BACKUP_ROOT is None:
            await self._send(user, "gitlab_paths_unconfigured")
            return
        await self._send(
            user,
            "gitlab_path_information",
            native_path=code(str(self.settings.GITLAB_BACKUP_ROOT)),
            staging_path=code(str(self.settings.GITLAB_STAGING_ROOT)),
        )

    async def _path_prompt(self, user: User, item: Conversation) -> None:
        prompt = t(user.language_code, "ask_paths")
        if item.data.get("location") is SourceLocation.LOCAL:
            paths = await asyncio.to_thread(available_worker_paths, self.settings)
            path_list = (
                TelegramHtml("\n".join(code(path) for path in paths))
                if paths
                else t(user.language_code, "no_available_paths")
            )
            available = t(user.language_code, "available_paths", paths=path_list)
            prompt = TelegramHtml(f"{prompt}\n\n{available}")
        current = item.data.get("paths") or item.data.get("existing_files_paths")
        if current and item.data.get("location") is SourceLocation.LOCAL:
            mapper = LocalSourcePathMapper(
                self.settings.allowed_source_roots(),
                self.settings.denied_source_roots(),
                self.settings.local_source_mounts(),
            )
            translated = []
            for path in current:
                try:
                    translated.append(mapper.container_path(path))
                except ValueError:
                    continue
            current = translated
        if current:
            prompt = TelegramHtml(
                f"{prompt}\n\n{t(user.language_code, 'current_value')}\n"
                f"{code(chr(10).join(current))}"
            )
        await self.transport.send_text(
            user.telegram_user_id, prompt, buttons=cancel_back(user.language_code)
        )

    async def _schedule_mode_prompt(
        self, session: AsyncSession, user: User, item: Conversation
    ) -> None:
        stored = await SettingsService(session).get()
        current = item.data.get("schedule") or item.data.get("existing_schedule")
        current_text = TelegramHtml("")
        if current is not None:
            mode = (
                "schedule_cron" if current.kind is ScheduleKind.CRON else "schedule_interval_mode"
            )
            current_text = t(
                user.language_code,
                "current_schedule_mode",
                mode=t(user.language_code, mode),
                schedule=self._schedule_label(user.language_code, current),
            )
        await self._send(
            user,
            "choose_schedule_mode",
            timezone=stored.backup_timezone,
            current=current_text,
            buttons=schedule_modes(user.language_code),
        )

    async def _advance_mssql_server(
        self, session: AsyncSession, user: User, item: Conversation, state: State
    ) -> None:
        servers = await RemoteServerRepository(session).list_active(enabled_only=True)
        if not servers:
            await self._advance_to_schedule(session, user, item, state)
            return
        item.option_ids = {
            f"🖥 {server.name} · {index}": server.id for index, server in enumerate(servers, 1)
        }
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_MSSQL_SERVER)
        buttons = [[reply(label)] for label in item.option_ids]
        buttons += rows(user.language_code, ("none",), ("back", "cancel"))
        await self._send(user, "choose_remote_server", buttons=buttons)

    async def _advance_to_compression(self, user: User, item: Conversation, state: State) -> None:
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_COMPRESSION)
        await self._send(
            user,
            "compression_explanation",
            buttons=compression_types(user.language_code),
        )

    async def _advance_to_archive(self, user: User, item: Conversation, state: State) -> None:
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_ARCHIVE)
        await self._send(
            user,
            "archive_explanation",
            buttons=archive_types(user.language_code),
        )

    async def _advance_to_encryption(self, user: User, item: Conversation, state: State) -> None:
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_ENCRYPTION)
        await self._send(user, "choose_encryption", buttons=encryption_types(user.language_code))

    async def _prepare_new_backup_admins(
        self, session: AsyncSession, user: User, item: Conversation, state: State
    ) -> None:
        admins = await UserRepository(session).list_admins(enabled_only=True)
        item.option_ids = {
            f"⬜ {admin.display_name} · {index}": admin.id for index, admin in enumerate(admins, 1)
        }
        item.data["admin_names"] = {admin.id: admin.display_name for admin in admins}
        if "editing_profile_id" in item.data:
            existing = await AccessRepository(session).admin_ids(
                int(item.data["editing_profile_id"])
            )
            item.data["selected"] = existing & set(item.data["admin_names"])
        else:
            item.data["selected"] = set()
        await self.fsm.transition(user.id, state, State.ADD_BACKUP_ADMINS)
        await self._access_keyboard(user, item, admin_direction=False)

    async def _show_profile_summary(
        self, session: AsyncSession, user: User, item: Conversation
    ) -> None:
        selected: set[int] = item.data["selected"]
        admin_names = [item.data["admin_names"][admin_id] for admin_id in sorted(selected)]
        engine = item.data.get("engine")
        schedule = item.data["schedule"]
        compression = item.data["compression"]
        encryption = item.data["encryption"]
        settings = await SettingsService(session).get()
        source_details = TelegramHtml("")
        if item.data["source_type"] is SourceType.FILES_DIRECTORIES:
            paths = file_source_path_lines(user.language_code, item.data["paths"])
            location_key = (
                "source_location_local"
                if item.data["location"] is SourceLocation.LOCAL
                else "source_location_remote"
            )
            source_details = t(
                user.language_code,
                "file_source_details",
                location=t(user.language_code, location_key),
                paths=paths,
            )
        elif engine is not None:
            source_details = t(
                user.language_code,
                "database_source_details",
                engine=self._engine_label(user.language_code, engine),
            )
        elif item.data["source_type"] is SourceType.GITLAB:
            location_key = (
                "source_location_local"
                if item.data["gitlab_location"] is SourceLocation.LOCAL
                else "source_location_remote"
            )
            detail_key = (
                "gitlab_source_details_local"
                if item.data["gitlab_location"] is SourceLocation.LOCAL
                else "gitlab_source_details_remote"
            )
            source_details = t(
                user.language_code,
                detail_key,
                location=t(user.language_code, location_key),
                container=code(item.data["gitlab_container"]),
                backup_path=code(item.data.get("gitlab_backup_path", "")),
            )
        buttons = await self._backup_confirm_buttons(user, item)
        await self._send(
            user,
            "profile_confirmation",
            name=item.data["name"],
            source=self._source_label(user.language_code, item.data["source_type"]),
            engine=self._engine_label(user.language_code, engine),
            server=item.data.get("remote_server_name", t(user.language_code, "none")),
            schedule=self._schedule_label(user.language_code, schedule),
            timezone=timezone_label(datetime.now(UTC), settings.backup_timezone),
            source_details=source_details,
            archive_compression=self._archive_compression_sections(user.language_code, compression),
            encryption=self._encryption_label(user.language_code, encryption),
            status=t(user.language_code, "status_draft"),
            enabled=t(
                user.language_code,
                "yes" if item.data.get("enabled", True) else "no",
            ),
            admins=(
                TelegramHtml("\n".join(f"• {code(name)}" for name in admin_names))
                if admin_names
                else t(user.language_code, "none")
            ),
            buttons=buttons,
        )

    @staticmethod
    def _missing_profile_field(item: Conversation) -> str | None:
        required = ("name", "source_type", "schedule", "compression", "encryption", "selected")
        for field in required:
            if field not in item.data:
                return field
        compression = item.data["compression"]
        if not isinstance(compression, CompressionConfig):
            return "compression"
        if compression.archive_format is None:
            return "archive"
        if item.data["source_type"] is SourceType.DATABASE:
            required_database = (
                "engine",
                "database_host",
                "database_port",
                "database_name",
                "database_username",
                "tls_mode",
            )
            for field in required_database:
                if field not in item.data:
                    return "source"
        elif item.data["source_type"] is SourceType.FILES_DIRECTORIES:
            for field in ("location", "paths"):
                if field not in item.data:
                    return "source"
        elif item.data["source_type"] is SourceType.GITLAB:
            for field in ("gitlab_location", "gitlab_container"):
                if field not in item.data:
                    return "source"
            if item.data["gitlab_location"] is SourceLocation.REMOTE_SERVER and (
                "gitlab_remote_server_id" not in item.data or "gitlab_backup_path" not in item.data
            ):
                return "source"
        else:
            return "source"
        return None

    async def _confirm_backup_profile(
        self,
        session: AsyncSession,
        user: User,
        item: Conversation,
        action: str | None,
    ) -> None:
        draft_id = item.data.get("editing_profile_id")
        context = {
            "telegram_user_id": user.telegram_user_id,
            "callback_data": action,
            "current_state": item.state.value,
            "expected_state": State.ADD_BACKUP_CONFIRM.value,
            "draft_profile_id": draft_id,
        }
        if action != "confirm":
            log.warning(
                "backup_profile_confirm_failed",
                **context,
                validation_result="invalid_action",
                failure_reason="confirmation callback action did not match",
            )
            await self._send(user, "backup_confirm_invalid_state")
            return
        missing = self._missing_profile_field(item)
        if missing is not None:
            log.warning(
                "backup_profile_confirm_failed",
                **context,
                validation_result="missing_field",
                failure_reason=f"missing draft field: {missing}",
            )
            await self._send(user, "backup_confirm_missing", field=missing)
            return
        if self.cipher is None:
            log.error(
                "backup_profile_confirm_failed",
                **context,
                validation_result="configuration_error",
                failure_reason="secret cipher unavailable",
            )
            await self._send(user, "backup_confirm_unavailable")
            return
        encryption = item.data["encryption"]
        requires_age_secret = (
            encryption.mode is EncryptionMode.AGE_PASSPHRASE
            and not item.data.get("preserve_age_passphrase")
        )
        requires_database_secret = item.data[
            "source_type"
        ] is SourceType.DATABASE and not item.data.get("preserve_database_password")
        unavailable_field = None
        if requires_age_secret and not await self.fsm.secrets.contains(user.id, AGE_UNLOCK):
            unavailable_field = "encryption"
        elif requires_database_secret and not await self.fsm.secrets.contains(
            user.id, DATABASE_CREDENTIAL
        ):
            unavailable_field = "source credentials"
        if unavailable_field is not None:
            log.warning(
                "backup_profile_confirm_failed",
                **context,
                validation_result="expired_secret",
                failure_reason=f"ephemeral {unavailable_field} secret unavailable",
            )
            await self.fsm.clear(user.id)
            await self._send(user, "backup_setup_expired")
            return
        try:
            profile = await self._persist_profile(session, user, item)
        except (ConflictError, KeyError, ValueError) as exc:
            await session.rollback()
            await session.refresh(user)
            reason = " ".join(str(exc).split())[:500] or type(exc).__name__
            log.exception(
                "backup_profile_confirm_failed",
                **context,
                validation_result="persistence_rejected",
                failure_reason=reason,
            )
            await self._send(user, "backup_confirm_validation_failed", reason=reason)
            return
        except Exception as exc:
            await session.rollback()
            await session.refresh(user)
            log.exception(
                "backup_profile_confirm_failed",
                **context,
                validation_result="internal_error",
                failure_reason=type(exc).__name__,
            )
            await self._send(user, "backup_confirm_save_failed")
            return
        log.info(
            "backup_profile_confirm_succeeded",
            **context,
            validation_result="accepted",
            profile_id=profile.id,
        )
        await self.fsm.clear(user.id)
        await self._send(
            user,
            "backup_configuration_updated" if draft_id is not None else "backup_created",
            name=profile.name,
            buttons=backups_menu(user.language_code),
        )
        await self._show_backups(session, user)

    async def _backup_confirm_buttons(self, user: User, item: Conversation) -> list[list[Button]]:
        confirmation_id = secrets.randbits(63) or 1
        item.data["confirmation_id"] = confirmation_id
        return [
            [
                Button.inline(
                    t(user.language_code, "confirm"),
                    await self.callbacks.issue(user.id, "backup_confirm", confirmation_id),
                ),
                Button.inline(
                    t(user.language_code, "cancel"),
                    await self.callbacks.issue(user.id, "backup_confirm_cancel", confirmation_id),
                ),
            ]
        ]

    async def _persist_profile(self, session: AsyncSession, user: User, item: Conversation) -> Any:
        if self.cipher is None:
            raise ValueError("secret cipher unavailable")
        succeeded = False

        async def complete(operation: Awaitable[Any]) -> Any:
            nonlocal succeeded
            result = await operation
            succeeded = True
            return result

        try:
            encryption = item.data["encryption"]
            preserve_age = bool(item.data.get("preserve_age_passphrase"))
            if encryption.mode is EncryptionMode.AGE_PASSPHRASE and not preserve_age:
                encryption = EncryptionConfig(
                    EncryptionMode.AGE_PASSPHRASE,
                    passphrase=await self.fsm.secrets.get(user.id, AGE_UNLOCK),
                )
            common: dict[str, Any] = {
                "name": item.data["name"],
                "description": item.data.get("description"),
                "schedule": item.data["schedule"],
                "compression": item.data["compression"],
                "encryption": encryption,
                "admin_ids": item.data["selected"],
            }
            service = BackupService(
                session,
                self.cipher,
                allowed_local_roots=self.settings.allowed_source_roots(),
                denied_local_roots=self.settings.denied_source_roots(),
                local_source_mounts=self.settings.local_source_mounts(),
            )
            if item.data["source_type"] is SourceType.DATABASE:
                preserve_password = bool(item.data.get("preserve_database_password"))
                database = DatabaseConfig(
                    engine=item.data["engine"],
                    host=item.data["database_host"],
                    port=item.data["database_port"],
                    database_name=item.data["database_name"],
                    username=item.data["database_username"],
                    password=(
                        None
                        if preserve_password
                        else await self.fsm.secrets.get(user.id, DATABASE_CREDENTIAL)
                    ),
                    tls_mode=item.data["tls_mode"],
                    tls_ca_certificate=item.data.get("tls_ca_certificate"),
                    mssql_in_docker=bool(item.data.get("mssql_in_docker", False)),
                    mssql_backup_path=item.data.get("mssql_backup_path"),
                    mssql_host_backup_path=item.data.get("mssql_host_backup_path"),
                    artifact_remote_server_id=item.data.get("artifact_remote_server_id"),
                )
                if "editing_profile_id" in item.data:
                    return await complete(
                        service.update_configuration(
                            user,
                            int(item.data["editing_profile_id"]),
                            source_type=SourceType.DATABASE,
                            database=database,
                            files=None,
                            enabled=bool(item.data["enabled"]),
                            preserve_database_password=preserve_password,
                            preserve_age_passphrase=preserve_age,
                            **common,
                        )
                    )
                return await complete(
                    service.create_database_profile(user, database=database, **common)
                )
            if item.data["source_type"] is SourceType.GITLAB:
                gitlab = GitLabConfig(
                    item.data["gitlab_location"],
                    item.data["gitlab_container"],
                    item.data.get("gitlab_backup_path"),
                    item.data.get("gitlab_remote_server_id"),
                )
                if "editing_profile_id" in item.data:
                    return await complete(
                        service.update_configuration(
                            user,
                            int(item.data["editing_profile_id"]),
                            source_type=SourceType.GITLAB,
                            database=None,
                            files=None,
                            gitlab=gitlab,
                            enabled=bool(item.data["enabled"]),
                            preserve_age_passphrase=preserve_age,
                            **common,
                        )
                    )
                return await complete(service.create_gitlab_profile(user, gitlab=gitlab, **common))
            files = FilesConfig(
                location=item.data["location"],
                paths=item.data["paths"],
                remote_server_id=item.data.get("remote_server_id"),
            )
            if "editing_profile_id" in item.data:
                return await complete(
                    service.update_configuration(
                        user,
                        int(item.data["editing_profile_id"]),
                        source_type=SourceType.FILES_DIRECTORIES,
                        database=None,
                        files=files,
                        enabled=bool(item.data["enabled"]),
                        preserve_age_passphrase=preserve_age,
                        **common,
                    )
                )
            return await complete(service.create_files_profile(user, files=files, **common))
        finally:
            if succeeded:
                await self.fsm.secrets.clear(user.id)

    @staticmethod
    def _schedule_value(kind: ScheduleKind, text: str) -> ScheduleConfig:
        if kind in {
            ScheduleKind.EVERY_N_MINUTES,
            ScheduleKind.EVERY_N_HOURS,
            ScheduleKind.EVERY_N_DAYS,
        }:
            if not text.isascii() or not text.isdecimal():
                raise ValueError("invalid interval")
            return ScheduleConfig(kind, interval=int(text)).validate()
        if kind is ScheduleKind.CRON:
            return ScheduleConfig(kind, cron_expression=" ".join(text.split())).validate()
        parts = text.split()
        if kind is ScheduleKind.DAILY:
            return ScheduleConfig(kind, time=text).validate()
        if len(parts) != 2 or not parts[0].isascii() or not parts[0].isdecimal():
            raise ValueError("invalid scheduled value")
        if kind is ScheduleKind.WEEKLY:
            return ScheduleConfig(kind, time=parts[1], weekday=int(parts[0])).validate()
        return ScheduleConfig(kind, time=parts[1], monthday=int(parts[0])).validate()
