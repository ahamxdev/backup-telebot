from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.health_service import collect_operational_health
from application.settings_service import SettingsService
from database.models import User
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    settings_menu,
)
from presentation.telegram.run_actions import RunActionsMixin


class HealthSettingsMixin(RunActionsMixin):
    async def _settings(self, session: AsyncSession, user: User) -> None:
        language = user.language_code
        stored = await SettingsService(session).get()
        await self.transport.send_text(
            user.telegram_user_id,
            t(
                language,
                "settings_text",
                proxy=t(
                    language, "enabled" if self.settings.TELEGRAM_PROXY_ENABLED else "disabled"
                ),
                limit=self.settings.telegram_max_file_bytes(),
                timezone=stored.backup_timezone,
            ),
            buttons=settings_menu(language),
        )

    async def _health(self, session: AsyncSession, user: User) -> None:
        AdminService.require_superadmin(user)
        health = await collect_operational_health(session, self.settings)
        tool_count = len(health.tools)
        available_tools = sum(tool.available for tool in health.tools)
        await self.transport.send_text(
            user.telegram_user_id,
            t(
                user.language_code,
                "health_text",
                status=health.status.upper(),
                live="✅" if health.live else "❌",
                ready="✅" if health.ready else "❌",
                database="✅" if health.database else "❌",
                schema="✅" if health.schema_current else "❌",
                revision=f"{health.current_revision}/{health.head_revision}",
                worker="✅" if health.worker_alive else "❌",
                scheduler="✅" if health.scheduler_active else "⚠️",
                telegram="✅" if health.telegram_ready else "❌",
                disk=health.free_disk_bytes // 1_048_576,
                pending=health.pending_delivery_bytes // 1_048_576,
                queued=health.queued_runs,
                running=health.running_runs,
                failed=health.failed_runs,
                stale=health.stale_leases,
                retained=health.retained_failed_artifacts,
                tools=f"{available_tools}/{tool_count}",
            ),
        )
