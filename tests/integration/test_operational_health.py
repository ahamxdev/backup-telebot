from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from application.health_service import (
    collect_operational_health,
    touch_heartbeats,
)
from config.settings import Settings
from database.models import BackupProfile, BackupRun, User, utc_now
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    LanguageCode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceType,
)


async def _prepare(session: AsyncSession, settings: Settings) -> Settings:
    await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    await session.execute(text("INSERT INTO alembic_version VALUES ('0015')"))
    await session.commit()
    for root in (settings.DATA_DIR, settings.WORK_ROOT, settings.PENDING_DELIVERY_ROOT):
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return settings.model_copy(update={"MIN_FREE_DISK_BYTES": 0})


@pytest.mark.asyncio
async def test_healthy_snapshot_reports_all_readiness_components(
    session: AsyncSession, settings: Settings
) -> None:
    checked_at = utc_now()
    configured = await _prepare(session, settings)
    await touch_heartbeats(
        session,
        ("worker", "scheduler", "telegram_bot", "telegram_worker"),
        activity=("scheduler",),
        now=checked_at,
    )

    health = await collect_operational_health(session, configured, now=checked_at)

    assert health.live is True
    assert health.ready is True
    assert health.status == "healthy"
    assert health.database and health.schema_current
    assert health.current_revision == health.head_revision == "0015"
    assert health.worker_alive and health.scheduler_active and health.telegram_ready
    assert health.pending_delivery_bytes == 0
    assert health.queued_runs == health.running_runs == health.failed_runs == 0
    assert health.stale_leases == health.retained_failed_artifacts == 0


@pytest.mark.asyncio
async def test_degraded_snapshot_keeps_readiness_but_reports_failed_retained_artifact(
    session: AsyncSession, settings: Settings
) -> None:
    checked_at = utc_now()
    configured = await _prepare(session, settings)
    await touch_heartbeats(
        session,
        ("worker", "telegram_bot", "telegram_worker"),
        now=checked_at,
    )
    owner = User(
        telegram_user_id=9001,
        display_name="owner",
        role=Role.SUPERADMIN,
        language_code=LanguageCode.ENGLISH,
        enabled=True,
    )
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name="retained",
        description=None,
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=False,
        status=ProfileStatus.DRAFT,
        schedule_kind=ScheduleKind.MANUAL,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )
    session.add(profile)
    await session.flush()
    session.add(
        BackupRun(
            profile_id=profile.id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.FAILED,
            stage=RunStage.COMPLETE,
            cancel_requested=False,
            pending_directory="retained-owned-spool",
            final_size_bytes=4096,
            finished_at=checked_at,
        )
    )
    await session.commit()

    health = await collect_operational_health(session, configured, now=checked_at)

    assert health.ready is True
    assert health.status == "degraded"
    assert health.failed_runs == 1
    assert health.retained_failed_artifacts == 1
    assert health.pending_delivery_bytes == 4096
    assert {"failed_runs", "retained_failed_artifacts"} <= set(health.issues)


@pytest.mark.asyncio
async def test_failed_snapshot_reports_stale_runtime_dependencies(
    session: AsyncSession, settings: Settings
) -> None:
    checked_at = utc_now()
    configured = await _prepare(session, settings)
    stale = checked_at - timedelta(hours=1)
    await touch_heartbeats(
        session,
        ("worker", "telegram_bot", "telegram_worker"),
        now=stale,
    )

    health = await collect_operational_health(session, configured, now=checked_at)

    assert health.live is True
    assert health.ready is False
    assert health.status == "failed"
    assert health.worker_alive is False
    assert health.telegram_ready is False
    assert {"worker_stale", "telegram_not_ready"} <= set(health.issues)
