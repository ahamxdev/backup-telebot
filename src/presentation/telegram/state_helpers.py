from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.admin_service import AdminService
from application.profile_config import CompressionConfig, EncryptionConfig, ScheduleConfig
from config.settings import Settings
from database.models import User
from domain.enums import (
    DatabaseEngine,
    EncryptionMode,
    LanguageCode,
    ScheduleKind,
    SourceType,
)
from infrastructure.repositories import (
    AccessRepository,
    BackupRepository,
    UserRepository,
)
from infrastructure.telegram import TelegramTransport
from presentation.telegram.constants import PAGE_SIZE
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    main_menu,
    reply,
    rows,
)
from presentation.telegram.profile_summary import (
    archive_compression_sections,
    archive_summary,
    compression_summary,
)
from presentation.telegram.runtime import (
    CallbackStore,
    Conversation,
    FSMStore,
    RateLimiter,
    State,
)
from security import SecretCipher


class TelegramStateMixin:
    sessions: async_sessionmaker[AsyncSession]
    transport: TelegramTransport
    settings: Settings
    fsm: FSMStore
    callbacks: CallbackStore
    cipher: SecretCipher | None
    rate_limit: RateLimiter

    async def _selection(
        self, user: User, state: State, objects: list[Any], prompt: str, icon: str
    ) -> None:
        if not objects:
            await self._home(user, "no_choices")
            return
        item = await self.fsm.set(user.id, state)
        for index, obj in enumerate(objects, start=1):
            name = obj.display_name if hasattr(obj, "display_name") else obj.name
            item.option_ids[f"{icon} {name} · {index}"] = obj.id
        item.data.update({"page": 0, "view": "selection", "prompt": prompt})
        await self._render_selection(user, item)

    async def _render_selection(self, user: User, item: Conversation) -> None:
        labels = list(item.option_ids)
        page = int(item.data.get("page", 0))
        start = page * PAGE_SIZE
        buttons = [[reply(label)] for label in labels[start : start + PAGE_SIZE]]
        page_buttons = []
        if page > 0:
            page_buttons.append(reply(t(user.language_code, "previous_page")))
        if start + PAGE_SIZE < len(labels):
            page_buttons.append(reply(t(user.language_code, "next_page")))
        if page_buttons:
            buttons.append(page_buttons)
        buttons += rows(user.language_code, ("back", "home"))
        await self.transport.send_text(
            user.telegram_user_id,
            t(user.language_code, str(item.data["prompt"])),
            buttons=buttons,
        )

    async def _prepare_admin_access(
        self, session: AsyncSession, item: Conversation, admin_id: int
    ) -> None:
        profiles = await BackupRepository(session).list_active()
        item.option_ids = {f"⬜ {profile.name}": profile.id for profile in profiles}
        item.data["selected"] = await AccessRepository(session).profile_ids(admin_id)

    async def _prepare_backup_access(
        self, session: AsyncSession, item: Conversation, profile_id: int
    ) -> None:
        admins = await UserRepository(session).list_admins(enabled_only=True)
        item.option_ids = {
            f"⬜ {admin.display_name} · {index}": admin.id for index, admin in enumerate(admins, 1)
        }
        item.data["selected"] = await AccessRepository(session).admin_ids(profile_id)

    async def _handle_access_action(
        self,
        session: AsyncSession,
        user: User,
        item: Conversation,
        text: str,
        action: str | None,
        *,
        admin_direction: bool,
    ) -> bool:
        selected: set[int] = item.data["selected"]
        if action in {"save_access", "remove_all"}:
            if action == "remove_all":
                selected.clear()
            if admin_direction:
                await AdminService(session).assign(user, int(item.data["target_id"]), selected)
            else:
                await AdminService(session).assign_admins(
                    user, int(item.data["target_id"]), selected
                )
            await self.fsm.clear(user.id)
            return True
        unchecked = item.option_ids.get(text.replace("✅ ", "⬜ ", 1))
        if unchecked is None:
            raise ValueError("unknown option")
        selected.symmetric_difference_update({unchecked})
        await self._access_keyboard(user, item, admin_direction=admin_direction)
        return False

    async def _access_keyboard(
        self, user: User, item: Conversation, *, admin_direction: bool
    ) -> None:
        selected: set[int] = item.data["selected"]
        item.data.setdefault("page", 0)
        item.data["view"] = "access"
        item.data["admin_direction"] = admin_direction
        choices = list(item.option_ids.items())
        page = int(item.data["page"])
        start = page * PAGE_SIZE
        buttons = [
            [reply(("✅ " if object_id in selected else "⬜ ") + label.removeprefix("⬜ "))]
            for label, object_id in choices[start : start + PAGE_SIZE]
        ]
        page_buttons = []
        if page > 0:
            page_buttons.append(reply(t(user.language_code, "previous_page")))
        if start + PAGE_SIZE < len(choices):
            page_buttons.append(reply(t(user.language_code, "next_page")))
        if page_buttons:
            buttons.append(page_buttons)
        buttons += rows(
            user.language_code, ("save_access",), ("remove_all",), ("back",), ("cancel",)
        )
        await self._send(
            user,
            "select_backup_access" if admin_direction else "select_admin_access",
            buttons=buttons,
        )

    async def _paginate(self, user: User, item: Conversation, action: str) -> None:
        current = int(item.data.get("page", 0))
        last = max(0, (len(item.option_ids) - 1) // PAGE_SIZE)
        item.data["page"] = min(last, current + 1) if action == "next_page" else max(0, current - 1)
        if item.data.get("view") == "access":
            await self._access_keyboard(
                user, item, admin_direction=bool(item.data.get("admin_direction"))
            )
        else:
            await self._render_selection(user, item)

    async def _home(self, user: User, message_key: str) -> None:
        await self._send(user, message_key, buttons=main_menu(user.role, user.language_code))

    async def _send(self, user: User, key: str, *, buttons: Any = None, **values: Any) -> None:
        await self.transport.send_text(
            user.telegram_user_id, t(user.language_code, key, **values), buttons=buttons
        )

    @staticmethod
    def _numeric_id(text: str) -> int:
        if not text.isascii() or not text.isdecimal():
            raise ValueError("not numeric")
        value = int(text)
        if value <= 0 or value > 9_223_372_036_854_775_807:
            raise ValueError("invalid ID")
        return value

    @staticmethod
    def _port(text: str) -> int:
        if not text.isascii() or not text.isdecimal():
            raise ValueError("invalid port")
        value = int(text)
        if not 1 <= value <= 65535:
            raise ValueError("invalid port")
        return value

    @staticmethod
    def _source_label(language: LanguageCode, source_type: SourceType) -> str:
        keys = {
            SourceType.DATABASE: "source_database",
            SourceType.FILES_DIRECTORIES: "source_files_directories",
            SourceType.GITLAB: "source_gitlab",
        }
        return t(language, keys[source_type])

    @staticmethod
    def _engine_label(language: LanguageCode, engine: DatabaseEngine | None) -> str:
        keys = {
            DatabaseEngine.POSTGRESQL: "database_postgresql",
            DatabaseEngine.MYSQL_MARIADB: "database_mysql",
            DatabaseEngine.MSSQL: "database_mssql",
        }
        return t(language, keys[engine]) if engine is not None else t(language, "none")

    @staticmethod
    def _schedule_label(language: LanguageCode, schedule: ScheduleConfig) -> str:
        if schedule.kind is ScheduleKind.ONCE:
            return t(language, "schedule_once")
        if schedule.kind is ScheduleKind.EVERY_N_MINUTES:
            return t(language, "schedule_minutes_value", value=schedule.interval)
        if schedule.kind is ScheduleKind.EVERY_N_HOURS:
            return t(language, "schedule_hours_value", value=schedule.interval)
        if schedule.kind is ScheduleKind.EVERY_N_DAYS:
            return t(language, "schedule_days_value", value=schedule.interval)
        if schedule.kind is ScheduleKind.DAILY:
            return t(language, "schedule_daily_value", value=schedule.time)
        if schedule.kind is ScheduleKind.WEEKLY:
            return t(
                language,
                "schedule_weekly_value",
                day=schedule.weekday,
                time=schedule.time,
            )
        if schedule.kind is ScheduleKind.MONTHLY:
            return t(
                language,
                "schedule_monthly_value",
                day=schedule.monthday,
                time=schedule.time,
            )
        if schedule.kind is ScheduleKind.CRON:
            return t(language, "schedule_cron_value", value=schedule.cron_expression)
        return t(language, "schedule_manual")

    @staticmethod
    def _compression_label(language: LanguageCode, config: CompressionConfig) -> str:
        return compression_summary(language, config)

    @staticmethod
    def _archive_label(language: LanguageCode, config: CompressionConfig) -> str:
        return archive_summary(language, config)

    @staticmethod
    def _archive_compression_sections(language: LanguageCode, config: CompressionConfig) -> str:
        return archive_compression_sections(language, config)

    @staticmethod
    def _encryption_label(language: LanguageCode, config: EncryptionConfig) -> str:
        keys = {
            EncryptionMode.NONE: "encryption_none",
            EncryptionMode.AGE_PUBLIC_KEY: "encryption_age_public",
            EncryptionMode.AGE_PASSPHRASE: "encryption_age_" + "passphrase",
        }
        return t(language, keys[config.mode])

    @staticmethod
    def _selected_id(item: Conversation, text: str) -> int:
        selected = item.option_ids.get(text)
        if selected is None:
            raise ValueError("unknown selection")
        return selected
