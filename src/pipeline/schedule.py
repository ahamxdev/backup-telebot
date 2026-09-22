import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BackupProfile
from domain.enums import ScheduleKind
from pipeline.jobs import RunService


@dataclass(frozen=True)
class SchedulerResult:
    run_id: int
    status: str


def next_scheduled_time(profile: BackupProfile, after: datetime, timezone: str) -> datetime | None:
    if profile.schedule_kind in {ScheduleKind.MANUAL, ScheduleKind.ONCE}:
        return None
    after_utc = _as_utc(after)
    if profile.schedule_kind in {
        ScheduleKind.EVERY_N_MINUTES,
        ScheduleKind.EVERY_N_HOURS,
        ScheduleKind.EVERY_N_DAYS,
    }:
        if profile.schedule_interval is None:
            raise ValueError("interval schedule is incomplete")
        unit = {
            ScheduleKind.EVERY_N_MINUTES: timedelta(minutes=1),
            ScheduleKind.EVERY_N_HOURS: timedelta(hours=1),
            ScheduleKind.EVERY_N_DAYS: timedelta(days=1),
        }[profile.schedule_kind]
        return after_utc + unit * profile.schedule_interval
    if profile.schedule_time is None:
        raise ValueError("calendar schedule is incomplete")
    hour, minute = (int(value) for value in profile.schedule_time.split(":"))
    zone = ZoneInfo(timezone)
    local = after_utc.astimezone(zone)
    if profile.schedule_kind is ScheduleKind.DAILY:
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
    elif profile.schedule_kind is ScheduleKind.WEEKLY:
        if profile.schedule_weekday is None:
            raise ValueError("weekly schedule is incomplete")
        days = (profile.schedule_weekday - local.weekday()) % 7
        candidate = (local + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= local:
            candidate += timedelta(days=7)
    else:
        if profile.schedule_monthday is None:
            raise ValueError("monthly schedule is incomplete")
        year, month = local.year, local.month
        day = min(profile.schedule_monthday, calendar.monthrange(year, month)[1])
        candidate = local.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            month = month % 12 + 1
            year = year + (1 if month == 1 else 0)
            day = min(profile.schedule_monthday, calendar.monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
    return candidate.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Scheduler:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tick(self, now: datetime, timezone: str) -> list[SchedulerResult]:
        profiles = list(
            await self.session.scalars(
                select(BackupProfile).where(
                    BackupProfile.enabled.is_(True),
                    BackupProfile.archived_at.is_(None),
                    BackupProfile.schedule_kind != ScheduleKind.MANUAL,
                )
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
                queued = await service.enqueue_scheduled(profile.id, due)
                results.append(SchedulerResult(queued.run.id, queued.run.status.value))
                cursor = max(due, _as_utc(now))
                profile.next_scheduled_at = (
                    None
                    if profile.schedule_kind is ScheduleKind.ONCE
                    else next_scheduled_time(profile, cursor, timezone)
                )
        await self.session.commit()
        return results
