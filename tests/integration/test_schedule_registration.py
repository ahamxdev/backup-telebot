from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from application.backup_service import BackupService
from application.bootstrap_service import BootstrapService
from application.profile_config import (
    CompressionConfig,
    EncryptionConfig,
    GitLabConfig,
    ScheduleConfig,
)
from application.settings_service import SettingsService
from database.models import BackupProfile, BackupRun
from domain.enums import EncryptionMode, RunStatus, ScheduleKind, SourceLocation
from pipeline.schedule import Scheduler, next_scheduled_time

CREATED = datetime(2026, 1, 15, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize("kind", [ScheduleKind.EVERY_N_MINUTES, ScheduleKind.CRON])
async def test_registration_discovery_restart_and_exactly_one_slot(database, monkeypatch, kind):
    monkeypatch.setattr("application.backup_service.utc_now", lambda: CREATED)
    # The worker has already polled before the Telegram profile is created.
    async with database.session_factory() as worker:
        assert await Scheduler(worker).tick(CREATED, "UTC") == []
    async with database.session_factory() as bot:
        actor, _ = await BootstrapService(bot).bootstrap_superadmin(1001)
        await SettingsService(bot).set_backup_timezone(actor, "Asia/Tehran")
        profile = await BackupService(bot).create_gitlab_profile(
            actor,
            name="registered",
            description=None,
            gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab"),
            schedule=(
                ScheduleConfig(kind, interval=5)
                if kind is ScheduleKind.EVERY_N_MINUTES
                else ScheduleConfig(kind, cron_expression="30 3 * * *")
            ),
            compression=CompressionConfig(),
            encryption=EncryptionConfig(EncryptionMode.NONE),
            admin_ids=set(),
        )
        profile_id = profile.id
        due = profile.next_scheduled_at
        assert due is not None and due.tzinfo is UTC
        assert due == (
            CREATED if kind is ScheduleKind.EVERY_N_MINUTES else datetime(2026, 1, 16, tzinfo=UTC)
        )
        assert await bot.scalar(select(func.count(BackupRun.id))) == 0
    # Fresh sessions model polling and restarting with only database state.
    async with database.session_factory() as worker:
        assert await Scheduler(worker).tick(due - timedelta(seconds=1), "Europe/Berlin") == []
        first = await Scheduler(worker).tick(due, "Europe/Berlin")
        assert len(first) == 1
        run = await worker.get(BackupRun, first[0].run_id)
        assert run is not None and run.status is RunStatus.QUEUED
    async with database.session_factory() as restarted:
        assert await Scheduler(restarted).tick(due, "UTC") == []
        saved = await restarted.get(BackupProfile, profile_id)
        assert saved is not None and saved.next_scheduled_at is not None
        assert saved.next_scheduled_at.replace(tzinfo=UTC) > due
        assert await restarted.scalar(select(func.count(BackupRun.id))) == 1


@pytest.mark.parametrize(
    "kind,enabled",
    [
        (ScheduleKind.MANUAL, True),
        (ScheduleKind.CRON, False),
        (ScheduleKind.EVERY_N_MINUTES, False),
    ],
)
async def test_manual_and_disabled_never_queue(session, monkeypatch, kind, enabled):
    monkeypatch.setattr("application.backup_service.utc_now", lambda: CREATED)
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    schedule = (
        ScheduleConfig(kind, cron_expression="30 3 * * *")
        if kind is ScheduleKind.CRON
        else ScheduleConfig(kind, interval=5)
        if kind is ScheduleKind.EVERY_N_MINUTES
        else ScheduleConfig(kind)
    )
    service = BackupService(session)
    profile = await service.create_gitlab_profile(
        actor,
        name="inactive",
        description=None,
        gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab"),
        schedule=schedule,
        compression=CompressionConfig(),
        encryption=EncryptionConfig(EncryptionMode.NONE),
        admin_ids=set(),
    )
    if not enabled:
        await service.edit(actor, profile.id, enabled=False)
    assert profile.next_scheduled_at is None
    assert await Scheduler(session).tick(CREATED + timedelta(days=90), "UTC") == []
    assert await session.scalar(select(func.count(BackupRun.id))) == 0


async def test_cursor_and_run_roll_back_together(database, monkeypatch):
    monkeypatch.setattr("application.backup_service.utc_now", lambda: CREATED)
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await BackupService(session).create_gitlab_profile(
            actor,
            name="atomic",
            description=None,
            gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab"),
            schedule=ScheduleConfig(ScheduleKind.EVERY_N_MINUTES, interval=5),
            compression=CompressionConfig(),
            encryption=EncryptionConfig(EncryptionMode.NONE),
            admin_ids=set(),
        )
        profile_id = profile.id

    def fail(*args):
        raise RuntimeError("simulated failure after enqueue")

    with monkeypatch.context() as patch:
        patch.setattr("pipeline.schedule.next_scheduled_time", fail)
        async with database.session_factory() as session:
            with pytest.raises(RuntimeError, match="after enqueue"):
                await Scheduler(session).tick(CREATED, "UTC")
            await session.rollback()
    async with database.session_factory() as restarted:
        assert await restarted.scalar(select(func.count(BackupRun.id))) == 0
        profile = await restarted.get(BackupProfile, profile_id)
        assert profile.next_scheduled_at.replace(tzinfo=UTC) == CREATED
        assert len(await Scheduler(restarted).tick(CREATED, "UTC")) == 1


@pytest.mark.parametrize(
    "timezone,expected",
    [
        ("UTC", datetime(2026, 1, 15, 3, 30, tzinfo=UTC)),
        ("Asia/Tehran", datetime(2026, 1, 16, 0, 0, tzinfo=UTC)),
        ("Europe/Berlin", datetime(2026, 1, 15, 2, 30, tzinfo=UTC)),
    ],
)
def test_cron_0330_uses_backup_timezone(timezone, expected):
    profile = BackupProfile(schedule_kind=ScheduleKind.CRON, schedule_cron="30 3 * * *")
    assert next_scheduled_time(profile, CREATED, timezone) == expected


@pytest.mark.parametrize(
    "after,expected",
    [
        (datetime(2026, 3, 28, 23, tzinfo=UTC), datetime(2026, 3, 29, 1, 30, tzinfo=UTC)),
        (datetime(2026, 10, 24, 22, tzinfo=UTC), datetime(2026, 10, 25, 2, 30, tzinfo=UTC)),
    ],
)
def test_berlin_dst_preserves_0330_wall_time(after, expected):
    profile = BackupProfile(schedule_kind=ScheduleKind.CRON, schedule_cron="30 3 * * *")
    result = next_scheduled_time(profile, after, "Europe/Berlin")
    assert result == expected
    local = result.astimezone(ZoneInfo("Europe/Berlin"))
    assert (local.hour, local.minute) == (3, 30)


async def test_timezone_change_recalculates_cron_but_keeps_elapsed_interval(session, monkeypatch):
    monkeypatch.setattr("application.backup_service.utc_now", lambda: CREATED)
    monkeypatch.setattr("application.settings_service.utc_now", lambda: CREATED)
    actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
    profiles = []
    for index, schedule in enumerate(
        [
            ScheduleConfig(ScheduleKind.CRON, cron_expression="30 3 * * *"),
            ScheduleConfig(ScheduleKind.EVERY_N_HOURS, interval=2),
        ]
    ):
        profiles.append(
            await BackupService(session).create_gitlab_profile(
                actor,
                name=f"zone{index}",
                description=None,
                gitlab=GitLabConfig(SourceLocation.LOCAL, f"gitlab{index}"),
                schedule=schedule,
                compression=CompressionConfig(),
                encryption=EncryptionConfig(EncryptionMode.NONE),
                admin_ids=set(),
            )
        )
    interval_due = profiles[1].next_scheduled_at
    await SettingsService(session).set_backup_timezone(actor, "Asia/Tehran")
    assert profiles[0].next_scheduled_at == datetime(2026, 1, 16, tzinfo=UTC)
    assert profiles[1].next_scheduled_at.replace(tzinfo=UTC) == interval_due


async def test_failed_schedule_registration_leaves_no_partial_profile(database, monkeypatch):
    async with database.session_factory() as session:
        actor, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        await SettingsService(session).get()
        await session.commit()

        def fail(*args):
            raise RuntimeError("registration unavailable")

        monkeypatch.setattr("application.backup_service.next_scheduled_time", fail)
        with pytest.raises(RuntimeError, match="registration unavailable"):
            await BackupService(session).create_gitlab_profile(
                actor,
                name="incomplete",
                description=None,
                gitlab=GitLabConfig(SourceLocation.LOCAL, "gitlab"),
                schedule=ScheduleConfig(ScheduleKind.CRON, cron_expression="30 3 * * *"),
                compression=CompressionConfig(),
                encryption=EncryptionConfig(EncryptionMode.NONE),
                admin_ids=set(),
            )
        await session.rollback()
    async with database.session_factory() as check:
        assert await check.scalar(select(func.count(BackupProfile.id))) == 0
        assert await check.scalar(select(func.count(BackupRun.id))) == 0
