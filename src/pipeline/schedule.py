from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.scheduling import _as_utc, next_scheduled_time
from database.models import BackupProfile, SystemSettings
from domain.enums import ScheduleKind
from pipeline.jobs import RunService


@dataclass(frozen=True)
class SchedulerResult:
    run_id: int
    status: str


class Scheduler:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tick(self, now: datetime, timezone: str) -> list[SchedulerResult]:
        # Lock settings before profile rows, matching timezone edits. Re-read under
        # the lock so a timezone change cannot race a cursor registration.
        stored_timezone = await self.session.scalar(
            select(SystemSettings.backup_timezone).where(SystemSettings.id == 1).with_for_update()
        )
        timezone = stored_timezone or timezone
        profiles = list(
            await self.session.scalars(
                select(BackupProfile)
                .where(
                    BackupProfile.enabled.is_(True),
                    BackupProfile.archived_at.is_(None),
                    BackupProfile.schedule_kind != ScheduleKind.MANUAL,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        results: list[SchedulerResult] = []
        service = RunService(self.session)
        for profile in profiles:
            if profile.next_scheduled_at is None:
                if profile.schedule_kind is ScheduleKind.ONCE:
                    continue
                profile.next_scheduled_at = next_scheduled_time(profile, now, timezone)
                continue
            due = _as_utc(profile.next_scheduled_at)
            if due <= _as_utc(now):
                queued = await service.enqueue_scheduled(profile.id, due, commit=False)
                results.append(SchedulerResult(queued.run.id, queued.run.status.value))
                cursor = max(due, _as_utc(now))
                profile.next_scheduled_at = (
                    None
                    if profile.schedule_kind is ScheduleKind.ONCE
                    else next_scheduled_time(profile, cursor, timezone)
                )
        await self.session.commit()
        return results
