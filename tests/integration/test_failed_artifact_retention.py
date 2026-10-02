import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from database.models import (
    AuditEvent,
    BackupArtifact,
    BackupProfile,
    BackupRun,
    User,
)
from domain.enums import (
    ArtifactKind,
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
)
from pipeline.jobs import RunService
from pipeline.transforms import sha256_file
from pipeline.worker import (
    cleanup_expired_failed_artifacts,
    recover_recorded_filesystem,
)


async def make_failed_run(
    session,
    settings,
    sequence: int,
    cleanup_at: datetime,
) -> tuple[BackupRun, BackupArtifact, Path]:
    owner = User(
        telegram_user_id=300 + sequence,
        display_name=f"owner-{sequence}",
        role=Role.SUPERADMIN,
    )
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name=f"failed-{sequence}",
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
    run_uuid = f"deadbeef-0000-4000-8000-{sequence:012d}"
    directory = settings.PENDING_DELIVERY_ROOT / profile.name / f"stamp_{run_uuid[:8]}"
    directory.mkdir(parents=True)
    artifact_path = directory / "backup.tar"
    artifact_path.write_bytes(f"failed artifact {sequence}".encode())
    checksum = sha256_file(artifact_path)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "run_uuid": run_uuid,
                "filename": artifact_path.name,
                "size_bytes": artifact_path.stat().st_size,
                "sha256": checksum,
                "created_at": cleanup_at.isoformat(),
                "metadata": {},
            }
        )
    )
    run = BackupRun(
        uuid=run_uuid,
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.FAILED,
        stage=RunStage.COMPLETE,
        finished_at=cleanup_at - timedelta(hours=1),
        pending_directory=str(directory),
        failed_artifact_cleanup_at=cleanup_at,
        final_filename=artifact_path.name,
        final_size_bytes=artifact_path.stat().st_size,
        final_sha256=checksum,
    )
    session.add(run)
    await session.flush()
    artifact = BackupArtifact(
        run_id=run.id,
        kind=ArtifactKind.FINAL,
        relative_path=artifact_path.name,
        size_bytes=artifact_path.stat().st_size,
        sha256=checksum,
    )
    session.add(artifact)
    await session.commit()
    return run, artifact, directory


async def test_failed_artifact_retention_cleanup_and_accounting(
    database, session, settings
) -> None:
    eligible = datetime(2026, 9, 13, 12, tzinfo=UTC)
    run, artifact, directory = await make_failed_run(session, settings, 1, eligible)
    expected_size = run.final_size_bytes

    assert await RunService(session).pending_bytes() == expected_size
    await cleanup_expired_failed_artifacts(
        database.session_factory, settings, now=eligible - timedelta(hours=1)
    )
    assert directory.exists()
    await cleanup_expired_failed_artifacts(
        database.session_factory, settings, now=eligible - timedelta(seconds=1)
    )
    assert directory.exists()

    await cleanup_expired_failed_artifacts(database.session_factory, settings, now=eligible)
    await session.refresh(run)
    assert not directory.exists()
    assert run.pending_directory is None
    assert run.failed_artifact_cleaned_at is not None
    assert run.status is RunStatus.FAILED
    assert run.final_filename == "backup.tar"
    assert run.final_size_bytes == expected_size
    assert await session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "backup_run_retention_cleanup",
            AuditEvent.target_id == str(run.id),
        )
    )
    assert run.final_sha256 == artifact.sha256
    assert await session.get(BackupArtifact, artifact.id)
    assert await RunService(session).pending_bytes() == 0


async def test_restart_recovery_cleans_expired_artifact_idempotently(
    database, session, settings
) -> None:
    run, artifact, directory = await make_failed_run(
        session,
        settings,
        2,
        datetime.now(UTC) - timedelta(seconds=1),
    )

    await recover_recorded_filesystem(database.session_factory, settings)
    await recover_recorded_filesystem(database.session_factory, settings)

    await session.refresh(run)
    assert not directory.exists()
    assert run.pending_directory is None
    assert run.failed_artifact_cleaned_at is not None
    assert await session.get(BackupArtifact, artifact.id) is not None


async def test_cleanup_refuses_malformed_path_and_preserves_source(
    database, session, settings, tmp_path: Path
) -> None:
    due = datetime(2026, 9, 13, 12, tzinfo=UTC)
    run, _artifact, original = await make_failed_run(session, settings, 3, due)
    source = tmp_path / "source-data"
    source.mkdir()
    source_file = source / "valuable.txt"
    source_file.write_text("never delete source data")
    run.pending_directory = str(source)
    await session.commit()

    await cleanup_expired_failed_artifacts(database.session_factory, settings, now=due)

    await session.refresh(run)
    assert source_file.read_text() == "never delete source data"
    assert run.pending_directory == str(source)
    assert run.failed_artifact_cleaned_at is None
    assert original.exists()


async def test_run_cannot_delete_another_runs_spool(database, session, settings) -> None:
    due = datetime(2026, 9, 13, 12, tzinfo=UTC)
    attacker, _attacker_artifact, attacker_directory = await make_failed_run(
        session, settings, 4, due
    )
    victim, victim_artifact, victim_directory = await make_failed_run(
        session, settings, 5, due + timedelta(days=1)
    )
    attacker.pending_directory = str(victim_directory)
    attacker.final_filename = victim.final_filename
    attacker.final_size_bytes = victim.final_size_bytes
    attacker.final_sha256 = victim.final_sha256
    await session.commit()

    await cleanup_expired_failed_artifacts(database.session_factory, settings, now=due)

    await session.refresh(attacker)
    assert victim_directory.exists()
    assert (victim_directory / "backup.tar").exists()
    assert attacker.pending_directory == str(victim_directory)
    assert await session.get(BackupArtifact, victim_artifact.id) is not None
    assert attacker_directory.exists()


async def test_cleanup_refuses_checksum_mismatch(database, session, settings) -> None:
    due = datetime(2026, 9, 13, 12, tzinfo=UTC)
    run, _artifact, directory = await make_failed_run(session, settings, 6, due)
    (directory / "backup.tar").write_bytes(b"tampered")

    await cleanup_expired_failed_artifacts(database.session_factory, settings, now=due)

    await session.refresh(run)
    assert directory.exists()
    assert run.pending_directory == str(directory)
    assert run.failed_artifact_cleaned_at is None


async def test_operator_retry_resets_failed_part_budget_without_resetting_progress(
    session, settings
) -> None:
    from database.models import BackupDelivery
    from domain.enums import DeliveryStatus

    run, _artifact, directory = await make_failed_run(
        session, settings, 99, datetime.now(UTC) + timedelta(days=1)
    )
    owner = await session.scalar(select(User).where(User.role == Role.SUPERADMIN))
    delivery = BackupDelivery(
        run_id=run.id,
        admin_id=owner.id,
        status=DeliveryStatus.FAILED,
        attempts=10,
        part_attempts=10,
        next_part=1,
        last_error="TRANSIENT:ConnectionResetError",
    )
    session.add(delivery)
    await session.commit()
    result = await RunService(session).retry_failed(owner, run.id, settings.PENDING_DELIVERY_ROOT)
    assert result.resumed_delivery
    await session.refresh(delivery)
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.attempts == 0 and delivery.part_attempts == 0
    assert delivery.next_part == 1
    assert directory.exists()
