import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.admin_service import AdminService
from application.observability import record_run_transition
from application.run_config import serialize_run_config
from database.models import (
    AdminBackupAccess,
    BackupDelivery,
    BackupProfile,
    BackupRun,
    DatabaseSource,
    FilesSource,
    User,
    utc_now,
)
from domain.enums import DeliveryStatus, RunStage, RunStatus, RunTrigger
from infrastructure.repositories import AuditRepository, NotFoundError
from pipeline.failed_artifacts import (
    remove_verified_retained_artifact,
    verified_retained_artifact,
)

ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.DELIVERY_PENDING)


@dataclass(frozen=True)
class EnqueueResult:
    run: BackupRun
    created: bool


@dataclass(frozen=True)
class CancellationCleanup:
    run_id: int
    run_uuid: str
    workspace_path: str | None
    pending_directory: str | None


@dataclass(frozen=True)
class RunPage:
    items: tuple[BackupRun, ...]
    page: int
    pages: int
    total: int


@dataclass(frozen=True)
class RetryResult:
    run: BackupRun
    resumed_delivery: bool


class RunService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, actor: User) -> list[BackupRun]:
        AdminService.require_superadmin(actor)
        return list(
            await self.session.scalars(
                select(BackupRun)
                .where(BackupRun.status.in_(ACTIVE_STATUSES))
                .options(selectinload(BackupRun.profile))
                .order_by(BackupRun.created_at.desc(), BackupRun.id.desc())
            )
        )

    async def history(self, actor: User, page: int, page_size: int = 8) -> RunPage:
        AdminService.require_superadmin(actor)
        page = max(0, page)
        total = int(await self.session.scalar(select(func.count(BackupRun.id))) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages - 1)
        items = tuple(
            await self.session.scalars(
                select(BackupRun)
                .options(selectinload(BackupRun.profile))
                .order_by(BackupRun.created_at.desc(), BackupRun.id.desc())
                .offset(page * page_size)
                .limit(page_size)
            )
        )
        return RunPage(items, page, pages, total)

    async def details(self, actor: User, run_id: int) -> BackupRun:
        AdminService.require_superadmin(actor)
        run = await self.session.get(BackupRun, run_id)
        if run is None:
            raise NotFoundError("backup run not found")
        await self.session.refresh(run, ("profile", "artifacts", "deliveries"))
        return run

    async def enqueue_manual(self, actor: User, profile_id: int) -> EnqueueResult:
        AdminService.require_superadmin(actor)
        result = await self._enqueue(profile_id, RunTrigger.MANUAL, None)
        await AuditRepository(self.session).record(
            "backup_run_requested",
            actor_id=actor.id,
            target_type="backup_run",
            target_id=str(result.run.id),
            details={"created": result.created, "profile_id": profile_id},
        )
        await self.session.commit()
        return result

    async def enqueue_scheduled(self, profile_id: int, scheduled_for: datetime) -> EnqueueResult:
        return await self._enqueue(profile_id, RunTrigger.SCHEDULED, scheduled_for)

    async def _enqueue(
        self, profile_id: int, trigger: RunTrigger, scheduled_for: datetime | None
    ) -> EnqueueResult:
        profile = await self.session.scalar(
            select(BackupProfile)
            .where(BackupProfile.id == profile_id)
            .options(
                selectinload(BackupProfile.database_source).selectinload(
                    DatabaseSource.artifact_remote_server
                ),
                selectinload(BackupProfile.files_source).selectinload(FilesSource.remote_server),
                selectinload(BackupProfile.files_source).selectinload(FilesSource.paths),
                selectinload(BackupProfile.gitlab_source),
            )
            .with_for_update()
        )
        if profile is None or profile.archived_at is not None or not profile.enabled:
            raise NotFoundError("enabled backup profile not found")
        remote_server = None
        if profile.database_source is not None:
            remote_server = profile.database_source.artifact_remote_server
        elif profile.files_source is not None:
            remote_server = profile.files_source.remote_server
        if remote_server is not None:
            await self.session.refresh(remote_server, with_for_update=True)
        assigned_admin_ids = set(
            await self.session.scalars(
                select(AdminBackupAccess.admin_id).where(
                    AdminBackupAccess.backup_profile_id == profile_id
                )
            )
        )
        if trigger is RunTrigger.SCHEDULED and scheduled_for is not None:
            existing_slot = await self.session.scalar(
                select(BackupRun).where(
                    BackupRun.profile_id == profile_id,
                    BackupRun.trigger == trigger,
                    BackupRun.scheduled_for == scheduled_for,
                )
            )
            if existing_slot is not None:
                return EnqueueResult(existing_slot, False)
        active = await self.active_for_profile(profile_id)
        if active is not None:
            if trigger is RunTrigger.MANUAL:
                return EnqueueResult(active, False)
            return await self._record_skipped(profile_id, scheduled_for)
        run = BackupRun(
            uuid=str(uuid.uuid4()),
            profile_id=profile_id,
            trigger=trigger,
            scheduled_for=scheduled_for,
            status=RunStatus.QUEUED,
            stage=RunStage.QUEUED,
            cancel_requested=False,
            configuration_snapshot=serialize_run_config(profile, assigned_admin_ids),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(run)
                await self.session.flush()
        except IntegrityError:
            if trigger is RunTrigger.SCHEDULED and scheduled_for is not None:
                existing_slot = await self.session.scalar(
                    select(BackupRun).where(
                        BackupRun.profile_id == profile_id,
                        BackupRun.trigger == trigger,
                        BackupRun.scheduled_for == scheduled_for,
                    )
                )
                if existing_slot is not None:
                    return EnqueueResult(existing_slot, False)
            active = await self.active_for_profile(profile_id)
            if active is None:
                raise
            if trigger is RunTrigger.MANUAL:
                return EnqueueResult(active, False)
            return await self._record_skipped(profile_id, scheduled_for)
        await record_run_transition(
            self.session,
            "queued",
            run.id,
            status=RunStatus.QUEUED.value,
            stage=RunStage.QUEUED.value,
        )
        await self.session.commit()
        return EnqueueResult(run, True)

    async def _record_skipped(
        self, profile_id: int, scheduled_for: datetime | None
    ) -> EnqueueResult:
        skipped = BackupRun(
            uuid=str(uuid.uuid4()),
            profile_id=profile_id,
            trigger=RunTrigger.SCHEDULED,
            scheduled_for=scheduled_for,
            status=RunStatus.SKIPPED,
            stage=RunStage.COMPLETE,
            skip_reason="profile already running",
            cancel_requested=False,
            finished_at=utc_now(),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(skipped)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(BackupRun).where(
                    BackupRun.profile_id == profile_id,
                    BackupRun.trigger == RunTrigger.SCHEDULED,
                    BackupRun.scheduled_for == scheduled_for,
                )
            )
            if existing is None:
                raise
            return EnqueueResult(existing, False)
        await self.session.commit()
        return EnqueueResult(skipped, True)

    async def active_for_profile(self, profile_id: int) -> BackupRun | None:
        return cast(
            BackupRun | None,
            await self.session.scalar(
                select(BackupRun)
                .where(BackupRun.profile_id == profile_id, BackupRun.status.in_(ACTIVE_STATUSES))
                .order_by(BackupRun.id.desc())
            ),
        )

    async def claim(
        self, owner: str, lease_seconds: int, statuses: tuple[RunStatus, ...]
    ) -> BackupRun | None:
        now = utc_now()
        has_deliveries = (
            select(BackupDelivery.id).where(BackupDelivery.run_id == BackupRun.id).exists()
        )
        has_due_delivery = (
            select(BackupDelivery.id)
            .where(
                BackupDelivery.run_id == BackupRun.id,
                BackupDelivery.status == DeliveryStatus.PENDING,
                or_(
                    BackupDelivery.next_attempt_at.is_(None),
                    BackupDelivery.next_attempt_at <= now,
                ),
            )
            .exists()
        )
        candidate_query = (
            select(BackupRun.id)
            .where(
                BackupRun.status.in_(statuses),
                BackupRun.cancel_requested.is_(False),
                or_(BackupRun.lease_owner.is_(None), BackupRun.lease_expires_at < now),
                or_(
                    BackupRun.status != RunStatus.DELIVERY_PENDING,
                    ~has_deliveries,
                    has_due_delivery,
                ),
            )
            .order_by(BackupRun.created_at, BackupRun.id)
            .limit(1)
        )
        bind = self.session.get_bind()
        candidate: Any
        if bind.dialect.name == "postgresql":
            candidate_id = await self.session.scalar(
                candidate_query.with_for_update(skip_locked=True)
            )
            if candidate_id is None:
                await self.session.rollback()
                return None
            candidate = int(candidate_id)
        else:
            candidate = candidate_query.scalar_subquery()
        row = (
            await self.session.execute(
                update(BackupRun)
                .where(BackupRun.id == candidate)
                .values(
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .returning(BackupRun.id)
            )
        ).first()
        if row is None:
            await self.session.rollback()
            return None
        run = await self.session.get(BackupRun, int(row.id))
        await self.session.commit()
        return run

    async def claim_cancel_requested(self, owner: str, lease_seconds: int) -> BackupRun | None:
        now = utc_now()
        candidate_query = (
            select(BackupRun.id)
            .where(
                BackupRun.status.in_(ACTIVE_STATUSES),
                BackupRun.cancel_requested.is_(True),
                or_(BackupRun.lease_owner.is_(None), BackupRun.lease_expires_at < now),
            )
            .order_by(BackupRun.updated_at, BackupRun.id)
            .limit(1)
        )
        bind = self.session.get_bind()
        candidate: Any
        if bind.dialect.name == "postgresql":
            candidate_id = await self.session.scalar(
                candidate_query.with_for_update(skip_locked=True)
            )
            if candidate_id is None:
                await self.session.rollback()
                return None
            candidate = int(candidate_id)
        else:
            candidate = candidate_query.scalar_subquery()
        row = (
            await self.session.execute(
                update(BackupRun)
                .where(BackupRun.id == candidate)
                .values(
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .returning(BackupRun.id)
            )
        ).first()
        if row is None:
            await self.session.rollback()
            return None
        run = await self.session.get(BackupRun, int(row.id))
        await self.session.commit()
        return run

    async def finalize_cancel_requested(
        self, run_id: int, owner: str
    ) -> CancellationCleanup | None:
        now = utc_now()
        row = (
            await self.session.execute(
                update(BackupRun)
                .where(
                    BackupRun.id == run_id,
                    BackupRun.status.in_(ACTIVE_STATUSES),
                    BackupRun.cancel_requested.is_(True),
                    BackupRun.lease_owner == owner,
                )
                .values(
                    status=RunStatus.CANCELLED,
                    stage=RunStage.COMPLETE,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code="CANCELLED",
                    error_message="run cancelled",
                    updated_at=now,
                )
                .returning(
                    BackupRun.id,
                    BackupRun.uuid,
                    BackupRun.workspace_path,
                    BackupRun.pending_directory,
                )
            )
        ).first()
        if row is None:
            await self.session.rollback()
            return None
        await record_run_transition(
            self.session,
            "cancelled",
            int(row.id),
            status=RunStatus.CANCELLED.value,
            stage=RunStage.COMPLETE.value,
            error_code="CANCELLED",
        )
        await self.session.commit()
        return CancellationCleanup(
            run_id=int(row.id),
            run_uuid=str(row.uuid),
            workspace_path=cast(str | None, row.workspace_path),
            pending_directory=cast(str | None, row.pending_directory),
        )

    async def renew(self, run_id: int, owner: str, lease_seconds: int) -> bool:
        now = utc_now()
        result = await self.session.execute(
            update(BackupRun)
            .where(BackupRun.id == run_id, BackupRun.lease_owner == owner)
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
            .returning(BackupRun.id)
        )
        renewed = result.first() is not None
        await self.session.commit()
        return renewed

    async def release(self, run: BackupRun, owner: str) -> None:
        if run.lease_owner != owner:
            raise RuntimeError("run lease ownership lost")
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()

    async def request_cancel(self, actor: User, run_id: int) -> None:
        AdminService.require_superadmin(actor)
        now = utc_now()
        requested = (
            await self.session.execute(
                update(BackupRun)
                .where(BackupRun.id == run_id, BackupRun.status.in_(ACTIVE_STATUSES))
                .values(cancel_requested=True, updated_at=now)
                .returning(BackupRun.id)
            )
        ).first()
        if requested is None:
            await self.session.rollback()
            raise NotFoundError("active run not found")
        cancelled = await self.session.execute(
            update(BackupRun)
            .where(
                BackupRun.id == run_id,
                BackupRun.status == RunStatus.QUEUED,
                BackupRun.cancel_requested.is_(True),
                BackupRun.lease_owner.is_(None),
            )
            .values(
                status=RunStatus.CANCELLED,
                stage=RunStage.COMPLETE,
                finished_at=now,
                lease_expires_at=None,
                error_code="CANCELLED",
                error_message="run cancelled before execution",
                updated_at=now,
            )
            .returning(BackupRun.id)
        )
        if cancelled.first() is not None:
            await record_run_transition(
                self.session,
                "cancelled",
                run_id,
                status=RunStatus.CANCELLED.value,
                stage=RunStage.COMPLETE.value,
                error_code="CANCELLED",
            )
        await self.session.commit()

    async def retry_failed(
        self,
        actor: User,
        run_id: int,
        pending_root: Path,
    ) -> RetryResult:
        """Safely resume failed deliveries or create a new run before finalization."""
        AdminService.require_superadmin(actor)
        run = await self.details(actor, run_id)
        if run.status is not RunStatus.FAILED or run.lease_owner is not None:
            raise NotFoundError("terminal failed run is not retryable")
        if run.pending_directory is None:
            if any(delivery.status is DeliveryStatus.SUCCEEDED for delivery in run.deliveries):
                raise NotFoundError("failed run with successful deliveries cannot be recreated")
            result = await self.enqueue_manual(actor, run.profile_id)
            if not result.created:
                raise NotFoundError("backup profile already has an active run")
            return RetryResult(result.run, False)

        failed = [
            delivery for delivery in run.deliveries if delivery.status is DeliveryStatus.FAILED
        ]
        if not failed:
            raise NotFoundError("failed run has no retryable deliveries")
        await verified_retained_artifact(self.session, run, pending_root)
        if await self.active_for_profile(run.profile_id) is not None:
            raise NotFoundError("backup profile already has an active run")
        now = utc_now()
        resumed = (
            await self.session.execute(
                update(BackupRun)
                .where(
                    BackupRun.id == run.id,
                    BackupRun.status == RunStatus.FAILED,
                    BackupRun.lease_owner.is_(None),
                    BackupRun.pending_directory == run.pending_directory,
                )
                .values(
                    status=RunStatus.DELIVERY_PENDING,
                    stage=RunStage.PENDING_DELIVERY,
                    finished_at=None,
                    failed_artifact_cleanup_at=None,
                    error_code=None,
                    error_message=None,
                    cancel_requested=False,
                    updated_at=now,
                )
                .returning(BackupRun.id)
            )
        ).first()
        if resumed is None:
            raise NotFoundError("failed run changed before retry")
        for delivery in failed:
            delivery.status = DeliveryStatus.PENDING
            delivery.attempts = 0
            delivery.next_attempt_at = now
            delivery.last_error = None
        await AuditRepository(self.session).record(
            "backup_run_retry_requested",
            actor_id=actor.id,
            target_type="backup_run",
            target_id=str(run.id),
            details={"failed_recipients": len(failed)},
        )
        await self.session.commit()
        await self.session.refresh(run)
        return RetryResult(run, True)

    async def purge_failed_artifact(
        self,
        actor: User,
        run_id: int,
        pending_root: Path,
    ) -> None:
        AdminService.require_superadmin(actor)
        run = await self.details(actor, run_id)
        if (
            run.status is not RunStatus.FAILED
            or run.lease_owner is not None
            or run.pending_directory is None
        ):
            raise NotFoundError("terminal failed run has no purgeable artifact")
        await remove_verified_retained_artifact(self.session, run, pending_root)
        cleaned_at = utc_now()
        run.pending_directory = None
        run.failed_artifact_cleaned_at = cleaned_at
        await AuditRepository(self.session).record(
            "failed_artifact_operator_purged",
            actor_id=actor.id,
            target_type="backup_run",
            target_id=str(run.id),
            details={"verified": True},
        )
        await self.session.commit()

    async def pending_bytes(self) -> int:
        value = await self.session.scalar(
            select(func.coalesce(func.sum(BackupRun.final_size_bytes), 0)).where(
                BackupRun.pending_directory.is_not(None)
            )
        )
        return int(value or 0)
