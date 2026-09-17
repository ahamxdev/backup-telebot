import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from telethon.errors import BadRequestError, FloodWaitError

from application.run_config import serialize_run_config
from database.models import (
    AdminBackupAccess,
    BackupDelivery,
    BackupProfile,
    BackupRun,
    User,
)
from domain.enums import (
    CompressionFormat,
    DeliveryStatus,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceType,
)
from pipeline import jobs as jobs_module
from pipeline.delivery import DeliveryService
from pipeline.jobs import RunService
from pipeline.transforms import sha256_file


class FakeTransport:
    def __init__(
        self,
        fail: set[int] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self.fail = fail or set()
        self.errors = errors or {}
        self.sent: list[int] = []
        self.kwargs: list[dict[str, object]] = []

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        del file
        self.sent.append(entity)
        self.kwargs.append(kwargs)
        if entity in self.errors:
            raise self.errors[entity]
        if entity in self.fail:
            raise OSError("offline")
        media = SimpleNamespace(document=SimpleNamespace(id=100 + entity))
        return SimpleNamespace(id=200 + entity, media=media)


class CancelAfterFirstSendTransport(FakeTransport):
    def __init__(self, run: BackupRun) -> None:
        super().__init__()
        self.run = run

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        message = await super().send_file(entity, file, **kwargs)
        self.run.cancel_requested = True
        return message


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 12, 10, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def delivery_service(session, transport, settings, clock: Clock, max_attempts: int = 10):
    return DeliveryService(
        session,
        transport,
        settings.PENDING_DELIVERY_ROOT,
        max_attempts=max_attempts,
        retry_base_seconds=30,
        retry_max_seconds=60,
        retry_jitter_seconds=5,
        now=clock,
        jitter=lambda _start, end: end,
    )


def test_exponential_backoff_and_jitter_are_bounded(session, settings) -> None:
    service = delivery_service(session, FakeTransport(), settings, Clock())

    assert service._retry_delay(1) == 35
    assert service._retry_delay(2) == 60
    assert service._retry_delay(50) == 60


async def _pending_run(session, root: Path) -> tuple[BackupRun, list[User], Path]:
    owner = User(telegram_user_id=10, display_name="root", role=Role.SUPERADMIN)
    admins = [
        User(telegram_user_id=11, display_name="a", role=Role.ADMIN),
        User(telegram_user_id=12, display_name="b", role=Role.ADMIN),
    ]
    session.add_all([owner, *admins])
    await session.flush()
    profile = BackupProfile(
        name="delivery",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )
    session.add(profile)
    await session.flush()
    session.add_all(
        AdminBackupAccess(admin_id=admin.id, backup_profile_id=profile.id, created_by=owner.id)
        for admin in admins
    )
    run_uuid = "00000000-0000-4000-8000-0000deadbeef"
    directory = root / "delivery" / "stamp_00000000"
    directory.mkdir(parents=True)
    artifact = directory / "delivery.tar"
    artifact.write_bytes(b"payload")
    run = BackupRun(
        uuid=run_uuid,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
        pending_directory=str(directory),
        final_filename=artifact.name,
        final_size_bytes=artifact.stat().st_size,
        final_sha256=sha256_file(artifact),
    )
    session.add(run)
    await session.commit()
    return run, admins, artifact


async def test_superadmin_is_unconditional_and_profile_admins_are_filtered(
    session, settings
) -> None:
    run, admins, _artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    await session.execute(
        delete(AdminBackupAccess).where(AdminBackupAccess.admin_id == admins[1].id)
    )
    await session.commit()
    transport = FakeTransport()

    assert await delivery_service(session, transport, settings, Clock()).attempt(run)

    assert transport.sent == [10, admins[0].telegram_user_id]
    deliveries = list(
        await session.scalars(
            select(BackupDelivery)
            .where(BackupDelivery.run_id == run.id)
            .order_by(BackupDelivery.admin_id)
        )
    )
    assert [(item.admin_id, item.status) for item in deliveries] == [
        (1, DeliveryStatus.SUCCEEDED),
        (admins[0].id, DeliveryStatus.SUCCEEDED),
    ]


async def test_transient_retry_is_durable_and_does_not_resend_success(
    database, session, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    clock = Clock()
    monkeypatch.setattr(jobs_module, "utc_now", clock)
    first = FakeTransport({admins[1].telegram_user_id})
    assert not await delivery_service(session, first, settings, clock).attempt(run)
    assert artifact.exists()
    assert run.status is RunStatus.DELIVERY_PENDING
    deliveries = list(
        await session.scalars(
            select(BackupDelivery)
            .where(BackupDelivery.run_id == run.id)
            .order_by(BackupDelivery.admin_id)
        )
    )
    assert deliveries[0].status is DeliveryStatus.SUCCEEDED
    assert "📦 <b>Backup Completed</b>" in str(first.kwargs[0]["caption"])
    assert "<b>Timezone:</b> UTC (UTC+00:00)" in str(first.kwargs[0]["caption"])
    assert first.kwargs[0]["parse_mode"] == "html"
    assert deliveries[0].attempts == 1
    assert deliveries[1].status is DeliveryStatus.SUCCEEDED
    assert deliveries[2].status is DeliveryStatus.PENDING
    assert deliveries[2].attempts == 1
    assert deliveries[2].next_attempt_at is not None
    assert deliveries[2].next_attempt_at.replace(tzinfo=UTC) == clock.value + timedelta(seconds=35)

    async with database.session_factory() as restarted_session:
        assert (
            await RunService(restarted_session).claim(
                "restarted-worker", 120, (RunStatus.DELIVERY_PENDING,)
            )
            is None
        )
    clock.advance(34)
    async with database.session_factory() as restarted_session:
        assert (
            await RunService(restarted_session).claim(
                "restarted-worker", 120, (RunStatus.DELIVERY_PENDING,)
            )
            is None
        )
    clock.advance(1)
    async with database.session_factory() as restarted_session:
        restarted_service = RunService(restarted_session)
        claimed = await restarted_service.claim(
            "restarted-worker", 120, (RunStatus.DELIVERY_PENDING,)
        )
        assert claimed is not None and claimed.id == run.id
        await restarted_service.release(claimed, "restarted-worker")
    await session.refresh(run)

    second = FakeTransport()
    assert await delivery_service(session, second, settings, clock).attempt(run)
    assert second.sent == [admins[1].telegram_user_id]
    assert not artifact.exists()
    assert run.status is RunStatus.SUCCEEDED
    await session.refresh(deliveries[0])
    assert deliveries[0].attempts == 1


async def test_delivery_uses_scheduler_advanced_next_run_not_snapshot(session, settings) -> None:
    run, _admins, _artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    profile = await session.get(BackupProfile, run.profile_id)
    assert profile is not None
    profile.schedule_kind = ScheduleKind.EVERY_N_MINUTES
    profile.schedule_interval = 5
    profile.next_scheduled_at = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    await session.refresh(profile, attribute_names=["database_source", "files_source"])
    run.configuration_snapshot = serialize_run_config(profile, {1})
    profile.next_scheduled_at = datetime(2026, 9, 12, 10, 5, tzinfo=UTC)
    await session.commit()
    transport = FakeTransport()

    assert await delivery_service(session, transport, settings, Clock()).attempt(run)

    caption = str(transport.kwargs[0]["caption"])
    assert "<b>Next run:</b> <code>2026-09-12 10:05:00</code>" in caption
    assert "<b>Next run:</b> <code>2026-09-12 10:00:00</code>" not in caption


async def test_authorization_is_rechecked_before_retry(session, settings) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    clock = Clock()
    first = FakeTransport({admins[1].telegram_user_id})
    await delivery_service(session, first, settings, clock).attempt(run)
    admins[1].enabled = False
    await session.commit()
    clock.advance(35)
    second = FakeTransport()
    assert await delivery_service(session, second, settings, clock).attempt(run)
    assert second.sent == []
    assert not artifact.exists()


async def test_permanent_error_stops_automatic_retry_and_preserves_artifact(
    session, settings
) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    clock = Clock()
    transport = FakeTransport(
        errors={admins[1].telegram_user_id: BadRequestError(request=None, message="FILE_INVALID")}
    )

    assert not await delivery_service(session, transport, settings, clock).attempt(run)

    delivery = await session.scalar(
        select(BackupDelivery).where(
            BackupDelivery.run_id == run.id,
            BackupDelivery.admin_id == admins[1].id,
        )
    )
    assert delivery is not None
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is None
    assert delivery.last_error == "PERMANENT:BadRequestError"
    assert run.status is RunStatus.FAILED
    assert run.error_code == "DELIVERY_ACTION_REQUIRED"
    assert run.failed_artifact_cleanup_at is not None
    assert run.failed_artifact_cleanup_at.replace(tzinfo=UTC) == clock.value + timedelta(days=7)
    assert artifact.exists()


async def test_flood_wait_uses_telegram_delay_even_above_backoff_cap(session, settings) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    clock = Clock()
    transport = FakeTransport(
        errors={admins[1].telegram_user_id: FloodWaitError(request=None, capture=125)}
    )

    assert not await delivery_service(session, transport, settings, clock).attempt(run)

    delivery = await session.scalar(
        select(BackupDelivery).where(
            BackupDelivery.run_id == run.id,
            BackupDelivery.admin_id == admins[1].id,
        )
    )
    assert delivery is not None
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.next_attempt_at is not None
    assert delivery.next_attempt_at.replace(tzinfo=UTC) == clock.value + timedelta(seconds=125)
    assert delivery.last_error == "FLOOD_WAIT:FloodWaitError"
    assert artifact.exists()


async def test_max_total_attempts_stops_transient_retry(session, settings) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    clock = Clock()
    failing_id = admins[1].telegram_user_id

    assert not await delivery_service(
        session, FakeTransport({failing_id}), settings, clock, max_attempts=2
    ).attempt(run)
    clock.advance(35)
    assert not await delivery_service(
        session, FakeTransport({failing_id}), settings, clock, max_attempts=2
    ).attempt(run)

    delivery = await session.scalar(
        select(BackupDelivery).where(
            BackupDelivery.run_id == run.id,
            BackupDelivery.admin_id == admins[1].id,
        )
    )
    assert delivery is not None
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.attempts == 2
    assert delivery.next_attempt_at is None
    assert run.status is RunStatus.FAILED
    assert artifact.exists()


async def test_delivery_stops_at_cancel_boundary_and_preserves_success(session, settings) -> None:
    run, admins, artifact = await _pending_run(session, settings.PENDING_DELIVERY_ROOT)
    run.lease_owner = "delivery-worker"
    await session.commit()
    transport = CancelAfterFirstSendTransport(run)

    with pytest.raises(asyncio.CancelledError):
        await DeliveryService(session, transport, settings.PENDING_DELIVERY_ROOT).attempt(run)

    assert transport.sent == [10]
    assert artifact.exists()
    deliveries = list(
        await session.scalars(
            select(BackupDelivery)
            .where(BackupDelivery.run_id == run.id)
            .order_by(BackupDelivery.id)
        )
    )
    assert deliveries[0].status is DeliveryStatus.SUCCEEDED
    assert deliveries[1].status is DeliveryStatus.PENDING
    assert deliveries[2].status is DeliveryStatus.PENDING

    cleanup = await RunService(session).finalize_cancel_requested(run.id, "delivery-worker")
    assert cleanup is not None
    await session.refresh(run)
    await session.refresh(deliveries[0])
    assert run.status is RunStatus.CANCELLED
    assert deliveries[0].status is DeliveryStatus.SUCCEEDED
