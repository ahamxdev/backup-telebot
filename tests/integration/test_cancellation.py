import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from database.models import BackupDelivery, BackupProfile, BackupRun, User
from domain.enums import (
    CompressionFormat,
    DeliveryStatus,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
)
from pipeline.jobs import RunService
from pipeline.worker import PipelineRunner, recover_recorded_filesystem
from pipeline.workspace import create_workspace


def profile(name: str, owner: User) -> BackupProfile:
    return BackupProfile(
        name=name,
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )


def worker_settings(settings):
    return settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "MIN_FREE_DISK_BYTES": 0,
        }
    )


def make_pending_directory(root: Path, run_uuid: str) -> Path:
    directory = root / "cancel-profile" / f"stamp_{run_uuid[:8]}"
    directory.mkdir(parents=True)
    (directory / "artifact.tar").write_bytes(b"cancelled artifact")
    return directory


async def test_queued_cancel_is_terminal_and_profile_can_run_again(session) -> None:
    owner = User(telegram_user_id=201, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("queued-cancel", owner)
    session.add(item)
    await session.commit()
    service = RunService(session)

    first = await service.enqueue_manual(owner, item.id)
    await service.request_cancel(owner, first.run.id)
    await session.refresh(first.run)

    assert first.run.status is RunStatus.CANCELLED
    assert first.run.stage is RunStage.COMPLETE
    assert first.run.finished_at is not None
    assert first.run.lease_owner is None
    assert await service.active_for_profile(item.id) is None

    replacement = await service.enqueue_manual(owner, item.id)
    assert replacement.created
    assert replacement.run.id != first.run.id
    assert replacement.run.status is RunStatus.QUEUED


async def test_running_cancel_waits_for_lease_owner_and_is_fenced(session) -> None:
    owner = User(telegram_user_id=202, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("running-cancel", owner)
    session.add(item)
    await session.commit()
    service = RunService(session)
    enqueued = await service.enqueue_manual(owner, item.id)
    claimed = await service.claim("worker-a", 120, (RunStatus.QUEUED,))
    assert claimed is not None
    claimed.status = RunStatus.RUNNING
    claimed.stage = RunStage.BACKUP
    await session.commit()
    run_id = claimed.id

    await service.request_cancel(owner, enqueued.run.id)
    await session.refresh(claimed)
    assert claimed.cancel_requested
    assert claimed.status is RunStatus.RUNNING
    assert await service.finalize_cancel_requested(run_id, "worker-b") is None

    cleanup = await service.finalize_cancel_requested(run_id, "worker-a")
    assert cleanup is not None
    await session.refresh(claimed)
    assert claimed.status is RunStatus.CANCELLED
    assert claimed.lease_owner is None


async def test_worker_restart_reclaims_cancel_after_crash(database, session, settings) -> None:
    owner = User(telegram_user_id=203, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("crashed-cancel", owner)
    session.add(item)
    await session.commit()
    service = RunService(session)
    enqueued = await service.enqueue_manual(owner, item.id)
    claimed = await service.claim("dead-worker", 120, (RunStatus.QUEUED,))
    assert claimed is not None
    claimed.status = RunStatus.RUNNING
    claimed.stage = RunStage.COMPRESS
    workspace = create_workspace(settings.WORK_ROOT, claimed.uuid)
    claimed.workspace_path = str(workspace.root)
    await session.commit()
    run_id = claimed.id
    await service.request_cancel(owner, enqueued.run.id)
    claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    assert await service.claim("ordinary-worker", 120, (RunStatus.RUNNING,)) is None
    restarted = await service.claim_cancel_requested("restarted-worker", 120)
    assert restarted is not None
    await session.refresh(restarted)
    assert restarted.id == run_id

    runner = PipelineRunner(
        database.session_factory,
        worker_settings(settings),
        object(),
        "restarted-worker",
    )
    await runner.execute(run_id)
    await session.refresh(claimed)
    assert claimed.status is RunStatus.CANCELLED
    assert claimed.stage is RunStage.COMPLETE
    assert claimed.workspace_path is None
    assert not workspace.root.exists()


async def test_cancelled_restart_cleanup_removes_only_owned_paths(
    database, session, settings, tmp_path: Path
) -> None:
    owner = User(telegram_user_id=204, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("restart-cleanup", owner)
    session.add(item)
    await session.flush()
    cancelled_uuid = "00000000-0000-4000-8000-000000000204"
    other_uuid = "00000000-0000-4000-8000-000000000205"
    cancelled_workspace = create_workspace(settings.WORK_ROOT, cancelled_uuid)
    other_workspace = create_workspace(settings.WORK_ROOT, other_uuid)
    pending = make_pending_directory(settings.PENDING_DELIVERY_ROOT, cancelled_uuid)
    source = tmp_path / "source-data"
    source.write_text("must survive cancellation")
    run = BackupRun(
        uuid=cancelled_uuid,
        profile_id=item.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.CANCELLED,
        stage=RunStage.COMPLETE,
        cancel_requested=True,
        finished_at=datetime.now(UTC),
        workspace_path=str(cancelled_workspace.root),
        pending_directory=str(pending),
    )
    session.add(run)
    await session.commit()

    await recover_recorded_filesystem(database.session_factory, settings)
    await session.refresh(run)
    assert run.workspace_path is None
    assert run.pending_directory is None
    assert not cancelled_workspace.root.exists()
    assert not pending.exists()
    assert other_workspace.root.exists()
    assert source.read_text() == "must survive cancellation"


async def test_concurrent_workers_claim_and_finalize_cancel_once(
    database, session, settings
) -> None:
    owner = User(telegram_user_id=205, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("concurrent-cancel", owner)
    session.add(item)
    await session.flush()
    run = BackupRun(
        uuid="00000000-0000-4000-8000-000000000206",
        profile_id=item.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        stage=RunStage.BACKUP,
        cancel_requested=True,
    )
    session.add(run)
    await session.commit()

    async def claim(worker: str):
        async with database.session_factory() as worker_session:
            claimed = await RunService(worker_session).claim_cancel_requested(worker, 120)
            return worker, claimed.id if claimed is not None else None

    claims = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    winners = [(worker, run_id) for worker, run_id in claims if run_id is not None]
    assert len(winners) == 1
    winning_owner, winning_run_id = winners[0]
    losing_owner = "worker-b" if winning_owner == "worker-a" else "worker-a"

    async with database.session_factory() as losing_session:
        assert (
            await RunService(losing_session).finalize_cancel_requested(run.id, losing_owner) is None
        )
    async with database.session_factory() as winning_session:
        cleanup = await RunService(winning_session).finalize_cancel_requested(
            winning_run_id, winning_owner
        )
        assert cleanup is not None
    await session.refresh(run)
    assert run.status is RunStatus.CANCELLED
    assert run.lease_owner is None

    stale_runner = PipelineRunner(
        database.session_factory,
        worker_settings(settings),
        object(),
        losing_owner,
    )
    await stale_runner._fail(run.id, RunStatus.FAILED, "STALE_FAILURE", "stale worker")
    await session.refresh(run)
    assert run.status is RunStatus.CANCELLED


async def test_worker_failure_normalizes_integer_error_code(database, session, settings) -> None:
    owner = User(telegram_user_id=208, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    item = profile("integer-error-code", owner)
    session.add(item)
    await session.commit()
    queued = await RunService(session).enqueue_manual(owner, item.id)
    claimed = await RunService(session).claim("integer-code-worker", 120, (RunStatus.QUEUED,))
    assert claimed is not None
    runner = PipelineRunner(
        database.session_factory,
        worker_settings(settings),
        object(),
        "integer-code-worker",
    )

    await runner._fail(queued.run.id, RunStatus.FAILED, 10, "connection lost")

    await session.refresh(queued.run)
    assert queued.run.status is RunStatus.FAILED
    assert queued.run.error_code == "10"


async def test_successful_delivery_record_survives_cancellation(session) -> None:
    owner = User(telegram_user_id=206, display_name="root", role=Role.SUPERADMIN)
    admin = User(telegram_user_id=207, display_name="admin", role=Role.ADMIN)
    session.add_all((owner, admin))
    await session.flush()
    item = profile("delivery-record-cancel", owner)
    session.add(item)
    await session.flush()
    run = BackupRun(
        uuid="00000000-0000-4000-8000-000000000207",
        profile_id=item.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.DELIVERY_PENDING,
        stage=RunStage.PENDING_DELIVERY,
        cancel_requested=True,
        lease_owner="delivery-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    session.add(run)
    await session.flush()
    delivery = BackupDelivery(
        run_id=run.id,
        admin_id=admin.id,
        status=DeliveryStatus.SUCCEEDED,
        attempts=1,
        telegram_message_id=99,
    )
    session.add(delivery)
    await session.commit()

    cleanup = await RunService(session).finalize_cancel_requested(run.id, "delivery-worker")
    assert cleanup is not None
    await session.refresh(delivery)
    assert delivery.status is DeliveryStatus.SUCCEEDED
    assert delivery.telegram_message_id == 99
