import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from application.observability import record_run_transition
from application.run_config import restore_run_config
from config.settings import Settings
from database.models import (
    BackupProfile,
    BackupRun,
    DatabaseSource,
    MSSQLArtifactCleanup,
    utc_now,
)
from domain.enums import RunStage, RunStatus
from pipeline.acquisition import cleanup_mssql_server_artifact
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.failed_artifacts import remove_verified_retained_artifact
from pipeline.workspace import (
    remove_owned_directory,
    remove_workspace,
    validate_owned_directory,
)
from security import SecretCipher

log = structlog.get_logger()

_TERMINAL = (
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.SKIPPED,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def cleanup_expired_failed_artifacts(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    """Delete only expired, verified spool artifacts owned by terminal failed runs."""
    cleanup_time = now or utc_now()
    async with sessions() as session:
        runs = list(
            await session.scalars(
                select(BackupRun)
                .where(
                    BackupRun.status == RunStatus.FAILED,
                    BackupRun.pending_directory.is_not(None),
                    or_(
                        BackupRun.failed_artifact_cleanup_at.is_(None),
                        BackupRun.failed_artifact_cleanup_at <= cleanup_time,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for run in runs:
            pending_value = run.pending_directory
            if pending_value is None:
                continue
            if run.failed_artifact_cleanup_at is None:
                retained_from = run.finished_at or run.updated_at or cleanup_time
                run.failed_artifact_cleanup_at = _utc(retained_from) + timedelta(
                    seconds=settings.FAILED_ARTIFACT_RETENTION_SECONDS
                )
                continue
            if _utc(run.failed_artifact_cleanup_at) > cleanup_time:
                continue
            pending = Path(pending_value)
            try:
                owned = await asyncio.to_thread(
                    validate_owned_directory,
                    settings.PENDING_DELIVERY_ROOT,
                    pending,
                    run.uuid,
                )
                if not await asyncio.to_thread(owned.exists):
                    run.pending_directory = None
                    run.failed_artifact_cleaned_at = cleanup_time
                    await record_run_transition(
                        session,
                        "retention_cleanup",
                        run.id,
                        status=RunStatus.FAILED.value,
                        stage=RunStage.COMPLETE.value,
                    )
                    continue
                await remove_verified_retained_artifact(
                    session,
                    run,
                    settings.PENDING_DELIVERY_ROOT,
                )
            except Exception as exc:
                log.error(
                    "failed_artifact_cleanup_refused",
                    run_id=run.id,
                    error_type=type(exc).__name__,
                )
                run.failed_artifact_cleanup_at = cleanup_time + timedelta(
                    seconds=settings.FAILED_ARTIFACT_RETENTION_SECONDS
                )
                continue
            run.pending_directory = None
            run.failed_artifact_cleaned_at = cleanup_time
            await record_run_transition(
                session,
                "retention_cleanup",
                run.id,
                status=RunStatus.FAILED.value,
                stage=RunStage.COMPLETE.value,
            )
        await session.commit()


async def recover_recorded_filesystem(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Finish only cleanup operations explicitly owned by durable run records."""
    async with sessions() as session:
        runs = list(
            await session.scalars(
                select(BackupRun).where(
                    BackupRun.status.in_(_TERMINAL),
                    (
                        BackupRun.workspace_path.is_not(None)
                        | BackupRun.pending_directory.is_not(None)
                    ),
                )
            )
        )
        for run in runs:
            if run.workspace_path:
                await asyncio.to_thread(remove_workspace, settings.WORK_ROOT, run.uuid)
                run.workspace_path = None
            if run.status in {RunStatus.SUCCEEDED, RunStatus.CANCELLED} and run.pending_directory:
                pending = Path(run.pending_directory)
                if await asyncio.to_thread(pending.exists):
                    await asyncio.to_thread(
                        remove_owned_directory,
                        settings.PENDING_DELIVERY_ROOT,
                        pending,
                        run.uuid,
                    )
                run.pending_directory = None
                run.stage = RunStage.COMPLETE
        await session.commit()
    await cleanup_expired_failed_artifacts(sessions, settings)


async def cleanup_orphaned_mssql_artifacts(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Retry verified fenced MSSQL cleanup records without touching active attempts."""
    key = settings.MASTER_ENCRYPTION_KEY_B64
    if key is None:
        return
    cipher = SecretCipher(key.get_secret_value())
    now = utc_now()
    async with sessions() as session:
        records = list(
            await session.scalars(
                select(MSSQLArtifactCleanup)
                .where(
                    MSSQLArtifactCleanup.retrieved_at.is_not(None),
                    MSSQLArtifactCleanup.cleaned_at.is_(None),
                )
                .options(
                    selectinload(MSSQLArtifactCleanup.run)
                    .selectinload(BackupRun.profile)
                    .selectinload(BackupProfile.database_source)
                    .selectinload(DatabaseSource.artifact_remote_server)
                )
            )
        )
        for record in records:
            run = record.run
            if (
                run.status not in _TERMINAL
                and run.lease_owner is not None
                and run.lease_expires_at is not None
                and _utc(run.lease_expires_at) >= now
            ):
                continue
            profile = (
                restore_run_config(run.configuration_snapshot)
                if run.configuration_snapshot is not None
                else run.profile
            )
            source = profile.database_source
            if (
                source is None
                or source.engine.value != "MSSQL"
                or record.remote_server_id != source.artifact_remote_server_id
                or record.size_bytes is None
                or record.sha256 is None
            ):
                record.last_error = "MSSQL_CLEANUP_REFUSED"
                continue
            try:
                await cleanup_mssql_server_artifact(
                    source,
                    record.artifact_path,
                    record.operation_id,
                    record.size_bytes,
                    record.sha256,
                    cipher,
                    RunDeadline(settings.MAX_BACKUP_RUNTIME_SECONDS),
                )
            except PipelineError as exc:
                if exc.code == "MSSQL_CLEANUP_MISSING":
                    record.cleaned_at = now
                    record.last_error = None
                else:
                    record.last_error = str(exc.code)[:64]
                    log.warning(
                        "mssql_server_artifact_cleanup_deferred",
                        run_id=run.id,
                        error_code=record.last_error,
                    )
            except Exception as exc:
                record.last_error = "MSSQL_CLEANUP_FAILED"
                log.warning(
                    "mssql_server_artifact_cleanup_deferred",
                    run_id=run.id,
                    error_type=type(exc).__name__,
                )
            else:
                record.cleaned_at = now
                record.last_error = None
        await session.commit()
