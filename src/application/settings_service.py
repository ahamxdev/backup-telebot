from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from application.admin_service import AdminService
from application.profile_config import validate_timezone
from database.models import BackupProfile, SystemSettings, User
from domain.enums import ScheduleKind
from infrastructure.repositories import AuditRepository, SettingsRepository


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SystemSettings:
        return await SettingsRepository(self.session).get()

    async def set_backup_timezone(self, actor: User, timezone: str) -> SystemSettings:
        AdminService.require_superadmin(actor)
        settings = await self.get()
        settings.backup_timezone = validate_timezone(timezone.strip())
        await self.session.execute(
            update(BackupProfile)
            .where(BackupProfile.schedule_kind.not_in((ScheduleKind.MANUAL, ScheduleKind.ONCE)))
            .values(next_scheduled_at=None)
        )
        await AuditRepository(self.session).record(
            "backup_timezone_changed",
            actor_id=actor.id,
            target_type="system_settings",
            target_id="1",
            details={"backup_timezone": settings.backup_timezone},
        )
        await self.session.commit()
        return settings
