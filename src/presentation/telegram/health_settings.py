import platform
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.health_service import OperationalHealth, collect_operational_health
from application.settings_service import SettingsService
from database.models import User
from domain.enums import LanguageCode
from presentation.telegram.formatting import TelegramHtml, code
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import settings_menu
from presentation.telegram.run_actions import RunActionsMixin
from version import __version__


def health_values(language: LanguageCode, health: OperationalHealth) -> dict[str, object]:
    def status(passed: bool) -> TelegramHtml:
        return t(language, "health_healthy" if passed else "health_error")

    return {
        "status": t(
            language,
            {
                "healthy": "health_healthy",
                "degraded": "health_warning",
                "failed": "health_error",
            }[health.status],
        ),
        "live": status(health.live),
        "database": status(health.database),
        "backend": health.database_backend,
        "schema": status(health.schema_current),
        "revision": code(f"{health.current_revision} / {health.head_revision}"),
        "worker": status(health.worker_alive),
        "scheduler": (
            status(health.scheduler_active)
            if health.scheduled_profile_count
            else t(language, "health_idle")
        ),
        "telegram": status(health.telegram_ready),
        "storage": status(health.storage_ready),
        "disk": health.free_disk_bytes // 1_048_576,
        "used": health.used_disk_bytes // 1_048_576,
        "pending": health.pending_delivery_bytes // 1_048_576,
        "pending_count": health.pending_delivery_count,
        "profiles": health.profile_count,
        "queued": health.queued_runs,
        "running": health.running_runs,
        "failed": health.failed_runs,
        "stale": health.stale_leases,
        "retained": health.retained_failed_artifacts,
        "tools": f"{sum(tool.available for tool in health.tools)}/{len(health.tools)}",
        "checked": (health.checked_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC"),
        # There is no helper or proxy health probe; configuration is not a health check.
        "helper": t(
            language, "health_unverified" if health.helper_configured else "health_disabled"
        ),
    }


class HealthSettingsMixin(RunActionsMixin):
    async def _settings(self, session: AsyncSession, user: User) -> None:
        language = user.language_code
        stored = await SettingsService(session).get()
        try:
            health = await collect_operational_health(session, self.settings)
        except SQLAlchemyError:
            await self._send(user, "health_unavailable")
            return
        values = health_values(language, health)
        proxy = self.settings.TELEGRAM_PROXY_ENABLED
        values.update(
            environment=self.settings.APP_ENV,
            version=__version__,
            python=platform.python_version(),
            proxy=t(language, "enabled" if proxy else "disabled"),
            proxy_status=t(language, "health_unverified" if proxy else "health_disabled"),
            endpoint=(
                code(f"{self.settings.TELEGRAM_PROXY_HOST}:{self.settings.TELEGRAM_PROXY_PORT}")
                if proxy
                else t(language, "none")
            ),
            limit=self.settings.telegram_max_file_bytes(),
            timezone=code(stored.backup_timezone),
            utc=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            encryption=t(language, "enabled" if self.cipher is not None else "disabled"),
            directories=self._runtime_directories(),
        )
        await self.transport.send_text(
            user.telegram_user_id,
            t(language, "settings_text", **values),
            buttons=settings_menu(language),
        )

    def _runtime_directories(self) -> TelegramHtml:
        return TelegramHtml(
            "\n".join(
                code(path)
                for path in (
                    self.settings.DATA_DIR,
                    self.settings.WORK_ROOT,
                    self.settings.PENDING_DELIVERY_ROOT,
                )
            )
        )

    async def _health(self, session: AsyncSession, user: User) -> None:
        AdminService.require_superadmin(user)
        try:
            health = await collect_operational_health(session, self.settings)
        except SQLAlchemyError:
            await self._send(user, "health_unavailable")
            return
        values = health_values(user.language_code, health)
        values.update(
            proxy=t(
                user.language_code,
                "health_unverified" if self.settings.TELEGRAM_PROXY_ENABLED else "health_disabled",
            ),
            directories=self._runtime_directories(),
        )
        await self.transport.send_text(
            user.telegram_user_id,
            t(user.language_code, "health_text", **values),
        )
