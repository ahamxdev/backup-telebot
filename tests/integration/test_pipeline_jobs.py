from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from application.backup_service import BackupService
from database.models import BackupProfile, BackupRun, User
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceType,
)
from pipeline.jobs import RunService
from pipeline.schedule import Scheduler, next_scheduled_time


def profile(name: str, owner: User, schedule: ScheduleKind = ScheduleKind.MANUAL) -> BackupProfile:
    return BackupProfile(
        name=name,
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        schedule_kind=schedule,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )


@pytest.mark.asyncio
async def test_manual_and_scheduled_collision_and_concurrent_profiles(session) -> None:
    owner = User(telegram_user_id=1, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    first, second = profile("one", owner), profile("two", owner)
    session.add_all((first, second))
    await session.commit()
    service = RunService(session)

    manual = await service.enqueue_manual(owner, first.id)
    duplicate = await service.enqueue_manual(owner, first.id)
    scheduled = await service.enqueue_scheduled(first.id, datetime(2026, 9, 11, 10, tzinfo=UTC))
    other = await service.enqueue_manual(owner, second.id)

    assert manual.created and duplicate.run.id == manual.run.id
    assert scheduled.run.status is RunStatus.SKIPPED
    assert scheduled.run.skip_reason == "profile already running"
    assert other.created and other.run.profile_id == second.id


@pytest.mark.asyncio
async def test_database_constraint_enforces_one_active_run(session) -> None:
    owner = User(telegram_user_id=2, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("atomic", owner)
    session.add(item)
    await session.flush()
    session.add_all(
        [
            BackupRun(
                profile_id=item.id,
                trigger=RunTrigger.MANUAL,
                status=RunStatus.QUEUED,
                stage=RunStage.QUEUED,
            ),
            BackupRun(
                profile_id=item.id,
                trigger=RunTrigger.MANUAL,
                status=RunStatus.RUNNING,
                stage=RunStage.BACKUP,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.commit()


def test_schedule_timezone_and_all_kinds() -> None:
    owner = User(id=1, telegram_user_id=3, display_name="root", role=Role.SUPERADMIN)
    after = datetime(2026, 9, 11, 5, tzinfo=UTC)
    for kind in (
        ScheduleKind.EVERY_N_MINUTES,
        ScheduleKind.EVERY_N_HOURS,
        ScheduleKind.EVERY_N_DAYS,
    ):
        item = profile(kind.value, owner, kind)
        item.schedule_interval = 2
        assert next_scheduled_time(item, after, "Asia/Tehran") > after
    daily = profile("daily", owner, ScheduleKind.DAILY)
    daily.schedule_time = "10:30"
    assert next_scheduled_time(daily, after, "Asia/Tehran") == datetime(2026, 9, 11, 7, tzinfo=UTC)
    weekly = profile("weekly", owner, ScheduleKind.WEEKLY)
    weekly.schedule_time, weekly.schedule_weekday = "10:30", 5
    assert next_scheduled_time(weekly, after, "Asia/Tehran") > after
    monthly = profile("monthly", owner, ScheduleKind.MONTHLY)
    monthly.schedule_time, monthly.schedule_monthday = "10:30", 20
    assert next_scheduled_time(monthly, after, "Asia/Tehran") > after


@pytest.mark.asyncio
async def test_scheduler_runs_activation_immediately_then_advances_without_duplicates(
    session,
) -> None:
    owner = User(telegram_user_id=4, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("timer", owner, ScheduleKind.EVERY_N_MINUTES)
    item.schedule_interval = 10
    item.next_scheduled_at = datetime(2026, 9, 11, 5, tzinfo=UTC)
    session.add(item)
    await session.commit()
    scheduler = Scheduler(session)
    first = await scheduler.tick(datetime(2026, 9, 11, 5, tzinfo=UTC), "UTC")
    second = await scheduler.tick(datetime(2026, 9, 11, 5, tzinfo=UTC), "UTC")
    assert len(first) == 1
    assert second == []
    assert item.next_scheduled_at == datetime(2026, 9, 11, 5, 10, tzinfo=UTC)
    assert await session.scalar(select(func.count(BackupRun.id))) == 1


@pytest.mark.asyncio
async def test_enabling_is_immediate_but_enabled_edit_preserves_schedule(session) -> None:
    owner = User(telegram_user_id=40, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("activation", owner, ScheduleKind.EVERY_N_MINUTES)
    item.enabled = False
    item.schedule_interval = 5
    session.add(item)
    await session.commit()

    service = BackupService(session)
    activated = await service.edit(owner, item.id, enabled=True)
    activation_due = activated.next_scheduled_at
    assert activation_due is not None

    results = await Scheduler(session).tick(activation_due, "UTC")
    assert len(results) == 1
    scheduled_next = activated.next_scheduled_at
    assert scheduled_next == activation_due + timedelta(minutes=5)

    run = await session.get(BackupRun, results[0].run_id)
    assert run is not None
    run.status = RunStatus.SUCCEEDED
    run.stage = RunStage.COMPLETE
    await session.commit()
    edited = await service.edit(owner, item.id, enabled=True)

    assert edited.next_scheduled_at == scheduled_next


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed(session) -> None:
    owner = User(telegram_user_id=5, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("lease", owner)
    session.add(item)
    await session.commit()
    queued = await RunService(session).enqueue_manual(owner, item.id)
    queued.run.status = RunStatus.RUNNING
    queued.run.lease_owner = "dead-worker"
    queued.run.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    reclaimed = await RunService(session).claim("new-worker", 120, (RunStatus.RUNNING,))

    assert reclaimed and reclaimed.id == queued.run.id
    assert reclaimed.lease_owner == "new-worker"
