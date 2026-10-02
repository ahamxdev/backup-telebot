from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.profile_config import validate_timezone
from application.scheduling import next_scheduled_time
from database.models import BackupProfile, SystemSettings, User, utc_now
from domain.enums import ScheduleKind
from infrastructure.repositories import AuditRepository, SettingsRepository


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SystemSettings:
        return await SettingsRepository(self.session).get()

    async def set_backup_timezone(self, actor: User, timezone: str) -> SystemSettings:
        AdminService.require_superadmin(actor)
        settings = await SettingsRepository(self.session).get(for_update=True)
        settings.backup_timezone = validate_timezone(timezone.strip())
        now = utc_now()
        profiles = await self.session.scalars(
            select(BackupProfile)
            .where(
                BackupProfile.enabled.is_(True),
                BackupProfile.archived_at.is_(None),
                BackupProfile.schedule_kind.in_(
                    (
                        ScheduleKind.CRON,
                        ScheduleKind.DAILY,
                        ScheduleKind.WEEKLY,
                        ScheduleKind.MONTHLY,
                    )
                ),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for profile in profiles:
            profile.next_scheduled_at = next_scheduled_time(profile, now, settings.backup_timezone)
        await AuditRepository(self.session).record(
            "backup_timezone_changed",
            actor_id=actor.id,
            target_type="system_settings",
            target_id="1",
            details={"backup_timezone": settings.backup_timezone},
        )
        await self.session.commit()
        return settings
