import asyncio
import json
import uuid
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from application.observability import record_run_transition
from application.profile_config import CompressionConfig
from application.run_config import restore_run_config
from config.settings import Settings
from database.models import (
    BackupArtifact,
    BackupProfile,
    BackupRun,
    DatabaseSource,
    FilesSource,
    MSSQLArtifactCleanup,
    SystemSettings,
    utc_now,
)
from domain.enums import (
    ArchiveFormat,
    ArtifactKind,
    CompressionAlgorithm,
    CompressionFormat,
    DatabaseEngine,
    EncryptionMode,
    RunStage,
    RunStatus,
    SourceLocation,
)
from pipeline.acquisition import (
    _mssql_visible_execution_path,
    acquire,
    cleanup_mssql_server_artifact,
)
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.deadline import RunDeadline
from pipeline.delivery import DeliveryService
from pipeline.errors import PipelineError
from pipeline.finalize import (
    artifact_filename,
    finalize_artifact,
    pending_directory_path,
    recover_finalized,
)
from pipeline.jobs import CancellationCleanup, RunService
from pipeline.recovery import (
    cleanup_expired_failed_artifacts,
    cleanup_orphaned_mssql_artifacts,
    recover_recorded_filesystem,
)
from pipeline.resources import disk_has_capacity, ensure_disk_capacity
from pipeline.transforms import (
    algorithm_extension,
    archive_extension,
    compress_algorithm,
    create_archive,
    encrypt,
    sha256_file,
)
from pipeline.workspace import (
    create_workspace,
    remove_owned_directory,
    remove_workspace,
)
from security import SecretCipher

log = structlog.get_logger()

__all__ = [
    "PipelineRunner",
    "cleanup_expired_failed_artifacts",
    "cleanup_orphaned_mssql_artifacts",
    "disk_has_capacity",
    "entrypoint",
    "recover_recorded_filesystem",
    "run_worker",
]


class PipelineRunner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        transport: object,
        owner: str,
    ) -> None:
        self.sessions, self.settings, self.transport, self.owner = (
            sessions,
            settings,
            transport,
            owner,
        )
        key = settings.MASTER_ENCRYPTION_KEY_B64
        if key is None:
            raise ValueError("worker requires MASTER_ENCRYPTION_KEY_B64")
        self.cipher = SecretCipher(key.get_secret_value())
        self.remote_transfers = asyncio.Semaphore(settings.MAX_CONCURRENT_REMOTE_TRANSFERS)

    async def execute(self, run_id: int) -> None:
        parent = asyncio.current_task()
        if parent is None:
            raise RuntimeError("worker task is unavailable")
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, lease_lost, parent))
        try:
            async with self.sessions() as session:
                run = await self._load(session, run_id)
                if run.cancel_requested:
                    raise asyncio.CancelledError
                if run.status is RunStatus.DELIVERY_PENDING:
                    await self._attempt_delivery(session, run)
                    return
                deadline = RunDeadline(
                    self.settings.MAX_BACKUP_RUNTIME_SECONDS,
                    started_at=run.started_at,
                )
                await deadline.wait_for(self._build(session, run, deadline))
                await self._attempt_delivery(session, run)
        except asyncio.CancelledError:
            if lease_lost.is_set():
                log.error("run_lease_lost", run_id=run_id)
                return
            if await self._finalize_cancel_requested(run_id):
                return
            raise
        except Exception as exc:
            if lease_lost.is_set():
                log.error("run_lease_lost", run_id=run_id)
                return
            if await self._finalize_cancel_requested(run_id):
                return
            raw_code = getattr(exc, "code", None)
            await self._fail(
                run_id,
                RunStatus.FAILED,
                "PIPELINE_FAILED" if raw_code is None else str(raw_code),
                str(exc),
            )
            await cleanup_orphaned_mssql_artifacts(self.sessions, self.settings)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _load(self, session: AsyncSession, run_id: int) -> BackupRun:
        run = await session.scalar(
            select(BackupRun)
            .where(BackupRun.id == run_id)
            .options(
                selectinload(BackupRun.profile).selectinload(BackupProfile.database_source),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.database_source)
                .selectinload(DatabaseSource.artifact_remote_server),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.files_source)
                .selectinload(FilesSource.remote_server),
                selectinload(BackupRun.profile)
                .selectinload(BackupProfile.files_source)
                .selectinload(FilesSource.paths),
                selectinload(BackupRun.profile).selectinload(BackupProfile.gitlab_source),
            )
        )
        if run is None:
            raise ValueError("run disappeared")
        return run

    async def _configuration(self, session: AsyncSession, run: BackupRun) -> BackupProfile:
        if run.configuration_snapshot is not None:
            return restore_run_config(run.configuration_snapshot)
        # Compatibility for runs queued before migration 0013. New runs always have a snapshot.
        return run.profile

    async def _stage(self, session: AsyncSession, run: BackupRun, stage: RunStage) -> None:
        await session.refresh(run, ("cancel_requested", "lease_owner"))
        if run.lease_owner != self.owner:
            raise RuntimeError("run lease ownership lost")
        if run.cancel_requested:
            raise asyncio.CancelledError
        run.stage = stage
        await session.commit()

    async def _ownership_boundary(self, session: AsyncSession, run: BackupRun) -> None:
        await session.refresh(run, ("cancel_requested", "lease_owner"))
        if run.lease_owner != self.owner:
            raise RuntimeError("run lease ownership lost")
        if run.cancel_requested:
            raise asyncio.CancelledError

    async def _remove_run_workspace(self, run_uuid: str) -> None:
        stop = CooperativeStop()
        await run_cooperative_thread(
            lambda: remove_workspace(self.settings.WORK_ROOT, run_uuid), stop
        )

    async def _cleanup_mssql_record(
        self,
        session: AsyncSession,
        run: BackupRun,
        record: MSSQLArtifactCleanup,
        deadline: RunDeadline,
        profile: BackupProfile | None = None,
    ) -> str | None:
        profile = profile or await self._configuration(session, run)
        source = profile.database_source
        if (
            source is None
            or source.engine is not DatabaseEngine.MSSQL
            or record.remote_server_id != source.artifact_remote_server_id
            or record.retrieved_at is None
            or record.size_bytes is None
            or record.sha256 is None
        ):
            record.last_error = "MSSQL_CLEANUP_REFUSED"
            await session.commit()
            return record.last_error
        await self._ownership_boundary(session, run)
        try:
            await cleanup_mssql_server_artifact(
                source,
                record.artifact_path,
                record.operation_id,
                record.size_bytes,
                record.sha256,
                self.cipher,
                deadline,
            )
        except PipelineError as exc:
            if exc.code == "MSSQL_CLEANUP_MISSING":
                record.cleaned_at = utc_now()
                record.last_error = None
            else:
                record.last_error = str(exc.code)[:64]
                log.warning(
                    "mssql_server_artifact_cleanup_warning",
                    run_id=run.id,
                    error_code=record.last_error,
                )
        except Exception as exc:
            record.last_error = "MSSQL_CLEANUP_FAILED"
            log.warning(
                "mssql_server_artifact_cleanup_warning",
                run_id=run.id,
                error_type=type(exc).__name__,
            )
        else:
            await self._ownership_boundary(session, run)
            record.cleaned_at = utc_now()
            record.last_error = None
        await session.commit()
        return record.last_error

    async def _cleanup_previous_mssql_records(
        self,
        session: AsyncSession,
        run: BackupRun,
        profile: BackupProfile,
        deadline: RunDeadline,
    ) -> None:
        records = list(
            await session.scalars(
                select(MSSQLArtifactCleanup).where(
                    MSSQLArtifactCleanup.run_id == run.id,
                    MSSQLArtifactCleanup.retrieved_at.is_not(None),
                    MSSQLArtifactCleanup.cleaned_at.is_(None),
                )
            )
        )
        for record in records:
            await self._cleanup_mssql_record(session, run, record, deadline, profile)

    async def _build(
        self,
        session: AsyncSession,
        run: BackupRun,
        deadline: RunDeadline,
    ) -> None:
        profile = await self._configuration(session, run)
        if await self._resume_finalized(session, run, deadline):
            return
        first_start = run.status is RunStatus.QUEUED
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or utc_now()
        if first_start:
            await record_run_transition(
                session,
                "started",
                run.id,
                status=RunStatus.RUNNING.value,
                stage=RunStage.BACKUP.value,
            )
        await self._stage(session, run, RunStage.BACKUP)
        await self._remove_run_workspace(run.uuid)
        workspace = create_workspace(self.settings.WORK_ROOT, run.uuid)
        run.workspace_path = str(workspace.root)
        await session.commit()

        def check_processing_disk(required_bytes: int = 0) -> None:
            deadline.check()
            ensure_disk_capacity(
                workspace.root,
                self.settings.MIN_FREE_DISK_BYTES,
                required_bytes,
            )

        check_processing_disk()
        stored = await session.get(SystemSettings, 1)
        if stored is None:
            stored = SystemSettings(id=1)
            session.add(stored)
            await session.flush()
        remote = bool(
            (profile.files_source and profile.files_source.location is SourceLocation.REMOTE_SERVER)
            or (
                profile.database_source
                and profile.database_source.artifact_remote_server_id is not None
            )
        )
        operation_id = uuid.uuid4().hex
        cleanup_record: MSSQLArtifactCleanup | None = None
        database_source = profile.database_source
        if database_source is not None and database_source.engine is DatabaseEngine.MSSQL:
            await self._cleanup_previous_mssql_records(session, run, profile, deadline)
            cleanup_record = MSSQLArtifactCleanup(
                run_id=run.id,
                operation_id=operation_id,
                artifact_path=_mssql_visible_execution_path(database_source, operation_id),
                remote_server_id=database_source.artifact_remote_server_id,
            )
            session.add(cleanup_record)
            await session.commit()

        async def record_mssql_retrieval(size: int, checksum: str) -> None:
            if cleanup_record is None:
                raise RuntimeError("MSSQL cleanup record is unavailable")
            await self._ownership_boundary(session, run)
            cleanup_record.retrieved_at = utc_now()
            cleanup_record.size_bytes = size
            cleanup_record.sha256 = checksum
            cleanup_record.last_error = None
            await session.commit()

        if remote:
            async with self.remote_transfers:
                source = await acquire(
                    profile,
                    workspace.raw / "source",
                    self.settings,
                    self.cipher,
                    deadline,
                    lambda: self._ownership_boundary(session, run),
                    operation_id,
                    record_mssql_retrieval if cleanup_record is not None else None,
                    run.id,
                )
        else:
            source = await acquire(
                profile,
                workspace.raw / "source",
                self.settings,
                self.cipher,
                deadline,
                lambda: self._ownership_boundary(session, run),
                operation_id,
                record_mssql_retrieval if cleanup_record is not None else None,
                run.id,
            )
        if cleanup_record is not None:
            warning = await self._cleanup_mssql_record(
                session, run, cleanup_record, deadline, profile
            )
            source.metadata["server_artifact_cleanup"] = (
                "warning" if warning is not None else "succeeded"
            )
            if warning is not None:
                source.metadata["server_artifact_cleanup_error"] = warning
        run.source_metadata = json.dumps(source.metadata, sort_keys=True)
        check_processing_disk()
        await self._stage(session, run, RunStage.VERIFY_SOURCE)
        if not source.path.is_file() or source.path.stat().st_size == 0:
            raise ValueError("source backup is empty")
        await self._stage(session, run, RunStage.COMPRESS)
        archive_format = profile.archive_format
        compression_algorithm = profile.compression_algorithm
        compression_preset = profile.compression_preset
        # ORM callers from before migration 0012 may populate only the legacy field.
        if (
            archive_format is ArchiveFormat.TAR
            and compression_algorithm is CompressionAlgorithm.ZSTANDARD
            and profile.compression_format is not CompressionFormat.ZSTANDARD
        ):
            legacy = CompressionConfig(
                profile.compression_format,
                profile.compression_preset,
            )
            archive_format = legacy.archive_format
            compression_algorithm = legacy.algorithm
        if source.metadata.get("native_compression"):
            compression_algorithm = CompressionAlgorithm.NONE
            compression_preset = None
            source.metadata["secondary_compression_skipped"] = True
            run.source_metadata = json.dumps(source.metadata, sort_keys=True)
        archived = workspace.staging / f"source{archive_extension(archive_format)}"
        await create_archive(
            source.path,
            archived,
            archive_format,
            timeout=deadline.remaining(),
            capacity_check=check_processing_disk,
            timeout_provider=deadline.remaining,
        )
        if compression_algorithm is CompressionAlgorithm.NONE:
            compressed = archived
        else:
            compressed = workspace.staging / (
                f"source{archive_extension(archive_format)}"
                f"{algorithm_extension(compression_algorithm)}"
            )
            await compress_algorithm(
                archived,
                compressed,
                compression_algorithm,
                compression_preset,
                threads=stored.compression_threads,
                memory_mb=stored.compression_memory_limit_mb,
                timeout=deadline.remaining(),
                capacity_check=check_processing_disk,
                timeout_provider=deadline.remaining,
            )
        await self._stage(session, run, RunStage.ENCRYPT)
        encryption_ext = ".age" if profile.encryption_mode is not EncryptionMode.NONE else ""
        suffixes = archive_extension(archive_format) + algorithm_extension(compression_algorithm)
        encrypted = workspace.final / f"artifact{suffixes}{encryption_ext}"
        passphrase = None
        if profile.encrypted_age_passphrase:
            passphrase = self.cipher.decrypt(
                profile.encrypted_age_passphrase, context="backup-age-passphrase-v1"
            )
        recipients = tuple(json.loads(profile.age_recipients or "[]"))
        await encrypt(
            compressed,
            encrypted,
            profile.encryption_mode,
            recipients=recipients,
            passphrase=passphrase,
            timeout=deadline.remaining(),
            capacity_check=check_processing_disk,
            timeout_provider=deadline.remaining,
        )
        check_processing_disk()
        await self._stage(session, run, RunStage.CHECKSUM)
        now = utc_now()
        extensions = suffixes + encryption_ext
        filename = artifact_filename(profile.name, now, stored.backup_timezone, extensions)
        await self._stage(session, run, RunStage.FINALIZE)
        check_processing_disk()
        intended = pending_directory_path(
            self.settings.PENDING_DELIVERY_ROOT,
            profile.name,
            run.uuid,
            now,
            stored.backup_timezone,
        )
        run.pending_directory = str(intended)
        run.final_filename = filename
        await session.commit()
        finalize_stop = CooperativeStop(deadline.check)
        finalized = await run_cooperative_thread(
            lambda: finalize_artifact(
                encrypted,
                self.settings.PENDING_DELIVERY_ROOT,
                profile.name,
                run.uuid,
                now,
                stored.backup_timezone,
                filename,
                source.metadata,
                finalize_stop.check,
                self.settings.telegram_max_file_bytes(),
            ),
            finalize_stop,
        )
        await self._ownership_boundary(session, run)
        run.pending_directory = str(finalized.directory)
        run.final_filename = filename
        run.final_size_bytes = finalized.size
        run.final_sha256 = finalized.checksum
        run.status = RunStatus.DELIVERY_PENDING
        run.stage = RunStage.PENDING_DELIVERY
        manifest_size = (await asyncio.to_thread(finalized.manifest.stat)).st_size
        hash_stop = CooperativeStop(deadline.check)
        manifest_sha256 = await run_cooperative_thread(
            lambda: sha256_file(finalized.manifest, hash_stop.check), hash_stop
        )
        await self._ownership_boundary(session, run)
        session.add(
            BackupArtifact(
                run_id=run.id,
                kind=ArtifactKind.FINAL,
                relative_path=filename,
                size_bytes=finalized.size,
                sha256=finalized.checksum,
            )
        )
        session.add(
            BackupArtifact(
                run_id=run.id,
                kind=ArtifactKind.MANIFEST,
                relative_path="manifest.json",
                size_bytes=manifest_size,
                sha256=manifest_sha256,
            )
        )
        await record_run_transition(
            session,
            "delivery_pending",
            run.id,
            status=RunStatus.DELIVERY_PENDING.value,
            stage=RunStage.PENDING_DELIVERY.value,
        )
        await session.commit()
        log.info(
            "artifact_created",
            run_id=run.id,
            size_bytes=finalized.size,
        )
        await self._remove_run_workspace(run.uuid)
        run.workspace_path = None
        await session.commit()
        deadline.check()

    async def _attempt_delivery(self, session: AsyncSession, run: BackupRun) -> None:
        stored = await session.get(SystemSettings, 1)
        await DeliveryService(
            session,
            self.transport,  # type: ignore[arg-type]
            self.settings.PENDING_DELIVERY_ROOT,
            self.settings.UPLOAD_PROGRESS_INTERVAL_SECONDS,
            self.settings.telegram_max_file_bytes(),
            self.settings.TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
            self.settings.TELEGRAM_MAX_DELIVERY_ATTEMPTS,
            self.settings.TELEGRAM_RETRY_BASE_SECONDS,
            self.settings.TELEGRAM_RETRY_MAX_SECONDS,
            self.settings.TELEGRAM_RETRY_JITTER_SECONDS,
            self.settings.FAILED_ARTIFACT_RETENTION_SECONDS,
            timezone=stored.backup_timezone if stored is not None else "UTC",
        ).attempt(run)

    async def _resume_finalized(
        self, session: AsyncSession, run: BackupRun, deadline: RunDeadline
    ) -> bool:
        deadline.check()
        if run.pending_directory is None:
            return False
        recovered = await asyncio.to_thread(
            recover_finalized,
            self.settings.PENDING_DELIVERY_ROOT,
            Path(run.pending_directory),
            run.uuid,
        )
        if recovered is None:
            run.pending_directory = None
            run.final_filename = None
            await session.commit()
            return False
        finalized, metadata = recovered
        previous_status = run.status
        run.final_filename = finalized.artifact.name
        run.final_size_bytes = finalized.size
        run.final_sha256 = finalized.checksum
        run.source_metadata = json.dumps(metadata, sort_keys=True)
        run.status = RunStatus.DELIVERY_PENDING
        run.stage = RunStage.PENDING_DELIVERY
        kinds = set(
            await session.scalars(
                select(BackupArtifact.kind).where(BackupArtifact.run_id == run.id)
            )
        )
        if ArtifactKind.FINAL not in kinds:
            session.add(
                BackupArtifact(
                    run_id=run.id,
                    kind=ArtifactKind.FINAL,
                    relative_path=finalized.artifact.name,
                    size_bytes=finalized.size,
                    sha256=finalized.checksum,
                )
            )
        if ArtifactKind.MANIFEST not in kinds:
            session.add(
                BackupArtifact(
                    run_id=run.id,
                    kind=ArtifactKind.MANIFEST,
                    relative_path="manifest.json",
                    size_bytes=(await asyncio.to_thread(finalized.manifest.stat)).st_size,
                    sha256=await asyncio.to_thread(sha256_file, finalized.manifest),
                )
            )
        if previous_status is not RunStatus.DELIVERY_PENDING:
            await record_run_transition(
                session,
                "delivery_pending",
                run.id,
                status=RunStatus.DELIVERY_PENDING.value,
                stage=RunStage.PENDING_DELIVERY.value,
            )
        await session.commit()
        await self._remove_run_workspace(run.uuid)
        run.workspace_path = None
        await session.commit()
        deadline.check()
        return True

    async def _heartbeat(
        self, run_id: int, lease_lost: asyncio.Event, parent: asyncio.Task[None]
    ) -> None:
        try:
            renew_interval = self.settings.JOB_LEASE_SECONDS / 3
            next_renewal = asyncio.get_running_loop().time() + renew_interval
            while True:
                await asyncio.sleep(min(1.0, renew_interval))
                async with self.sessions() as session:
                    state = (
                        await session.execute(
                            select(BackupRun.lease_owner, BackupRun.cancel_requested).where(
                                BackupRun.id == run_id
                            )
                        )
                    ).first()
                    if state is None or state.lease_owner != self.owner:
                        lease_lost.set()
                        parent.cancel()
                        return
                    if state.cancel_requested:
                        parent.cancel()
                        return
                    if asyncio.get_running_loop().time() < next_renewal:
                        continue
                    if not await RunService(session).renew(
                        run_id, self.owner, self.settings.JOB_LEASE_SECONDS
                    ):
                        lease_lost.set()
                        parent.cancel()
                        return
                    next_renewal = asyncio.get_running_loop().time() + renew_interval
        except asyncio.CancelledError:
            raise
        except Exception:
            lease_lost.set()
            parent.cancel()

    async def _finalize_cancel_requested(self, run_id: int) -> bool:
        async with self.sessions() as session:
            cleanup = await RunService(session).finalize_cancel_requested(run_id, self.owner)
        if cleanup is None:
            return False
        workspace_cleaned = cleanup.workspace_path is None
        pending_cleaned = cleanup.pending_directory is None
        if cleanup.workspace_path is not None:
            try:
                await self._remove_run_workspace(cleanup.run_uuid)
                workspace_cleaned = True
            except Exception:
                log.exception("cancelled_workspace_cleanup_failed", run_id=run_id)
        if cleanup.pending_directory is not None:
            pending = Path(cleanup.pending_directory)
            try:
                if await asyncio.to_thread(pending.exists):
                    await asyncio.to_thread(
                        remove_owned_directory,
                        self.settings.PENDING_DELIVERY_ROOT,
                        pending,
                        cleanup.run_uuid,
                    )
                pending_cleaned = True
            except Exception:
                log.exception("cancelled_pending_cleanup_failed", run_id=run_id)
        if workspace_cleaned or pending_cleaned:
            await self._clear_cancelled_paths(cleanup, workspace_cleaned, pending_cleaned)
        await cleanup_orphaned_mssql_artifacts(self.sessions, self.settings)
        return True

    async def _clear_cancelled_paths(
        self,
        cleanup: CancellationCleanup,
        workspace_cleaned: bool,
        pending_cleaned: bool,
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(BackupRun, cleanup.run_id)
            if run is None or run.status is not RunStatus.CANCELLED:
                return
            if workspace_cleaned and run.workspace_path == cleanup.workspace_path:
                run.workspace_path = None
            if pending_cleaned and run.pending_directory == cleanup.pending_directory:
                run.pending_directory = None
            await session.commit()

    async def _fail(self, run_id: int, status: RunStatus, code: object, message: str) -> None:
        safe_code = str("PIPELINE_FAILED" if code is None else code)[:64]
        async with self.sessions() as session:
            run = await session.get(BackupRun, run_id)
            if (
                run is None
                or run.status
                not in {
                    RunStatus.QUEUED,
                    RunStatus.RUNNING,
                    RunStatus.DELIVERY_PENDING,
                }
                or run.lease_owner != self.owner
            ):
                return
            if run.status is RunStatus.DELIVERY_PENDING:
                if safe_code in {
                    "PENDING_ARTIFACT_MISSING",
                    "PENDING_CHECKSUM_MISMATCH",
                    "UPLOAD_LIMIT",
                }:
                    run.status = RunStatus.FAILED
                    run.stage = RunStage.COMPLETE
                    run.finished_at = utc_now()
                run.lease_owner = None
                run.lease_expires_at = None
            else:
                await self._remove_run_workspace(run.uuid)
                run.workspace_path = None
                run.status = status
                run.stage = RunStage.COMPLETE
                run.finished_at = utc_now()
                run.lease_owner = None
                run.lease_expires_at = None
            run.error_code = safe_code
            run.error_message = message[:2000]
            if run.status is RunStatus.FAILED and run.pending_directory is not None:
                run.failed_artifact_cleanup_at = utc_now() + timedelta(
                    seconds=self.settings.FAILED_ARTIFACT_RETENTION_SECONDS
                )
            if run.status is RunStatus.FAILED:
                await record_run_transition(
                    session,
                    "failed",
                    run.id,
                    status=RunStatus.FAILED.value,
                    stage=RunStage.COMPLETE.value,
                    error_code=run.error_code,
                )
            await session.commit()


async def run_worker(settings: Settings) -> None:
    """Compatibility entry point for the relocated worker bootstrap."""
    from bootstrap.worker import run_worker as bootstrap_worker

    await bootstrap_worker(settings)


def entrypoint() -> None:
    """Compatibility entry point for existing module-based invocations."""
    from bootstrap.worker import entrypoint as bootstrap_entrypoint

    bootstrap_entrypoint()
