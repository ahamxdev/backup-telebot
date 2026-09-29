import asyncio
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telethon.errors import FloodError, RPCError, ServerError, TimedOutError

from application.observability import record_run_transition
from application.run_config import restore_run_admin_ids, restore_run_config
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
from domain.enums import DeliveryStatus, Role, RunStage, RunStatus, ScheduleKind, SourceLocation
from infrastructure.repositories import AccessRepository
from pipeline.errors import PipelineError
from pipeline.finalize import delivery_artifacts
from pipeline.workspace import remove_owned_directory
from presentation.telegram.datetime_format import format_user_timestamp, timezone_label
from presentation.telegram.formatting import TelegramHtml, code, escaped, format_bytes_iec

log = structlog.get_logger()


class DeliveryTransport(Protocol):
    async def send_file(self, entity: int, file: str, **kwargs: Any) -> Any: ...


class DeliveryErrorKind(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    FLOOD_WAIT = "FLOOD_WAIT"


_RANDOM = secrets.SystemRandom()


def _schedule_caption(profile: BackupProfile) -> str:
    labels = {
        ScheduleKind.MANUAL: "Manual only",
        ScheduleKind.ONCE: "Once",
        ScheduleKind.EVERY_N_MINUTES: f"Every {profile.schedule_interval} minutes",
        ScheduleKind.EVERY_N_HOURS: f"Every {profile.schedule_interval} hours",
        ScheduleKind.EVERY_N_DAYS: f"Every {profile.schedule_interval} days",
        ScheduleKind.DAILY: f"Daily at {profile.schedule_time}",
        ScheduleKind.WEEKLY: f"Weekly, day {profile.schedule_weekday} at {profile.schedule_time}",
        ScheduleKind.MONTHLY: (
            f"Monthly, day {profile.schedule_monthday} at {profile.schedule_time}"
        ),
    }
    return labels[profile.schedule_kind]


def build_backup_caption(
    run: BackupRun, profile: BackupProfile, completed_at: datetime, timezone: str
) -> TelegramHtml:
    try:
        metadata = json.loads(run.source_metadata or "{}")
    except (TypeError, ValueError):
        metadata = {}
    schedule_lines = [f"⏰ <b>Schedule:</b> {escaped(_schedule_caption(profile))}"]
    if profile.next_scheduled_at is not None:
        schedule_lines.append(
            f"⏭ <b>Next run:</b> {format_user_timestamp(profile.next_scheduled_at, timezone)}"
        )
    schedule_lines.append(f"🌍 <b>Timezone:</b> {escaped(timezone_label(completed_at, timezone))}")
    sections = [
        "📦 <b>Backup Completed</b>",
        "━━━━━━━━━━━━━━",
        f"📌 <b>Profile:</b> {code(profile.name)}",
        "✅ <b>Status:</b> Completed",
        f"⏱ <b>Started:</b> {format_user_timestamp(run.started_at, timezone)}",
        f"🏁 <b>Completed:</b> {format_user_timestamp(completed_at, timezone)}",
        "\n".join(schedule_lines),
        "━━━━━━━━━━━━━━",
    ]
    if profile.database_source is not None:
        database_source = profile.database_source
        engine = {
            "POSTGRESQL": "PostgreSQL",
            "MYSQL_MARIADB": "MySQL / MariaDB",
            "MSSQL": "Microsoft SQL Server",
        }[database_source.engine.value]
        engine_version = engine
        if database_source.engine.value == "POSTGRESQL" and metadata.get("server_major"):
            engine_version = f"PostgreSQL {metadata['server_major']}"
        elif metadata.get("server_version"):
            engine_version = f"{engine} {metadata['server_version']}"
        sections.extend(
            (
                "🌐 <b>Source:</b> Database",
                f"🗄 <b>Database Type:</b> {escaped(engine)}",
                f"⚙️ <b>Engine:</b> {escaped(engine_version)}",
                f"🖥 <b>Server:</b> {code(database_source.host)}",
                f"💾 <b>Database:</b> {code(database_source.database_name)}",
            )
        )
    elif profile.files_source is not None:
        files_source = profile.files_source
        remote = files_source.location is SourceLocation.REMOTE_SERVER
        sections.append(f"🌐 <b>Source:</b> {'Remote Server' if remote else 'Local Host'}")
        if remote and files_source.remote_server is not None:
            sections.append(f"🏷 <b>Server name:</b> {code(files_source.remote_server.name)}")
            sections.append(f"🖥 <b>Server:</b> {code(files_source.remote_server.host)}")
        for path in files_source.paths:
            sections.append(f"📂 <b>Path (File / Directory):</b> {code(path.path)}")
        skipped = [
            (int(metadata.get("skipped_symlinks", 0)), "unsafe symlink"),
            (int(metadata.get("skipped_special_files", 0)), "special file"),
            (int(metadata.get("skipped_unreadable_entries", 0)), "unreadable entry"),
        ]
        warning_parts = [
            f"{count} {label}{'' if count == 1 else 's'}" for count, label in skipped if count
        ]
        if warning_parts:
            sections.append(f"⚠️ <b>Skipped:</b> {escaped(', '.join(warning_parts))}")
    sections.extend((f"🆔 <b>Run ID:</b> {code(run.id)}", "━━━━━━━━━━━━━━"))
    original_size = (
        format_bytes_iec(run.original_size_bytes)
        if run.original_size_bytes is not None
        else "Unknown"
    )
    final_size = (
        format_bytes_iec(run.final_size_bytes) if run.final_size_bytes is not None else "Unknown"
    )
    sections.insert(-1, f"📦 <b>Original Backup Size:</b> {escaped(original_size)}")
    sections.insert(-1, f"🗜 <b>Final Backup Size:</b> {escaped(final_size)}")
    return TelegramHtml("\n".join(sections))


def classify_delivery_error(exc: Exception) -> tuple[DeliveryErrorKind, int | None]:
    if isinstance(exc, FloodError):
        seconds = getattr(exc, "seconds", None)
        return DeliveryErrorKind.FLOOD_WAIT, (max(0, int(seconds)) if seconds is not None else None)
    if isinstance(exc, (ServerError, TimedOutError, TimeoutError, OSError)):
        return DeliveryErrorKind.TRANSIENT, None
    if isinstance(exc, RPCError):
        return DeliveryErrorKind.PERMANENT, None
    return DeliveryErrorKind.PERMANENT, None


class DeliveryService:
    def __init__(
        self,
        session: AsyncSession,
        transport: DeliveryTransport,
        pending_root: Path,
        progress_interval: int = 5,
        max_bytes: int = 2_000_000_000,
        upload_timeout: int = 7200,
        max_attempts: int = 10,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
        retry_jitter_seconds: int = 15,
        failed_artifact_retention_seconds: int = 604800,
        timezone: str = "UTC",
        *,
        now: Callable[[], datetime] = utc_now,
        jitter: Callable[[float, float], float] = _RANDOM.uniform,
    ) -> None:
        self.session = session
        self.transport = transport
        self.pending_root = pending_root
        self.progress_interval = progress_interval
        self.max_bytes = max_bytes
        self.upload_timeout = upload_timeout
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.retry_jitter_seconds = retry_jitter_seconds
        self.failed_artifact_retention_seconds = failed_artifact_retention_seconds
        self.timezone = timezone
        self.now = now
        self.jitter = jitter

    async def _cancel_boundary(self, run: BackupRun) -> None:
        await self.session.refresh(run, ("cancel_requested",))
        if run.cancel_requested:
            raise asyncio.CancelledError

    @staticmethod
    def _is_due(delivery: BackupDelivery, now: datetime) -> bool:
        due = delivery.next_attempt_at
        if due is None:
            return True
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        return due <= now

    def _retry_delay(self, attempts: int, flood_wait: int | None = None) -> float:
        exponent = min(attempts - 1, 30)
        delay: float = float(min(self.retry_max_seconds, self.retry_base_seconds * (2**exponent)))
        jitter_limit = min(self.retry_jitter_seconds, max(0, self.retry_max_seconds - delay))
        if jitter_limit:
            delay += float(self.jitter(0, jitter_limit))
        if flood_wait is not None:
            delay = max(delay, flood_wait)
        return delay

    async def _refresh_next_scheduled_at(
        self, profile: BackupProfile, reference_time: datetime
    ) -> None:
        """Use only the scheduler-owned persisted cursor, never the queued snapshot cursor."""
        next_scheduled_at = await self.session.scalar(
            select(BackupProfile.next_scheduled_at).where(BackupProfile.id == profile.id)
        )
        if next_scheduled_at is not None:
            aware_next = (
                next_scheduled_at.replace(tzinfo=UTC)
                if next_scheduled_at.tzinfo is None
                else next_scheduled_at.astimezone(UTC)
            )
            aware_reference = (
                reference_time.replace(tzinfo=UTC)
                if reference_time.tzinfo is None
                else reference_time.astimezone(UTC)
            )
            profile.next_scheduled_at = aware_next if aware_next > aware_reference else None
        else:
            profile.next_scheduled_at = None

    async def attempt(self, run: BackupRun) -> bool:
        await self._cancel_boundary(run)
        if (
            not run.pending_directory
            or not run.final_filename
            or run.final_size_bytes is None
            or not run.final_sha256
        ):
            raise ValueError("run has no finalized artifact")
        pending_directory = Path(run.pending_directory)
        if run.configuration_snapshot is not None:
            profile = restore_run_config(run.configuration_snapshot)
        else:
            live_profile = await self.session.scalar(
                select(BackupProfile)
                .where(BackupProfile.id == run.profile_id)
                .options(
                    selectinload(BackupProfile.database_source).selectinload(
                        DatabaseSource.artifact_remote_server
                    ),
                    selectinload(BackupProfile.files_source).selectinload(
                        FilesSource.remote_server
                    ),
                    selectinload(BackupProfile.files_source).selectinload(FilesSource.paths),
                    selectinload(BackupProfile.gitlab_source),
                )
            )
            if live_profile is None:
                raise ValueError("run profile is unavailable")
            profile = live_profile
        await self._refresh_next_scheduled_at(profile, self.now())
        artifacts = await asyncio.to_thread(
            delivery_artifacts,
            pending_directory,
            run.final_filename,
            run.final_size_bytes,
            run.final_sha256,
        )
        if any(artifact.size > self.max_bytes for artifact in artifacts):
            raise PipelineError(
                "UPLOAD_LIMIT", "artifact part exceeds the configured Telegram upload limit"
            )
        live_assigned_ids = set(
            await self.session.scalars(
                select(AdminBackupAccess.admin_id).where(
                    AdminBackupAccess.backup_profile_id == run.profile_id
                )
            )
        )
        assigned_ids = (
            restore_run_admin_ids(run.configuration_snapshot) & live_assigned_ids
            if run.configuration_snapshot is not None
            else live_assigned_ids
        )
        assigned_admins = list(
            await self.session.scalars(
                select(User).where(
                    User.id.in_(assigned_ids),
                    User.role == Role.ADMIN,
                    User.enabled.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        )
        superadmins = list(
            await self.session.scalars(
                select(User).where(
                    User.role == Role.SUPERADMIN,
                    User.enabled.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        )
        recipient_reasons = {
            **{admin.id: "profile_access" for admin in assigned_admins},
            **{admin.id: "superadmin" for admin in superadmins},
        }
        selected_ids = set(recipient_reasons)
        log.info(
            "backup_delivery_recipients_selected",
            run_id=run.id,
            profile_id=run.profile_id,
            selected_recipients=[
                {"admin_id": admin_id, "reason": recipient_reasons[admin_id]}
                for admin_id in sorted(selected_ids)
            ],
            excluded_assigned_admin_ids=sorted(assigned_ids - selected_ids),
        )
        deliveries = {
            delivery.admin_id: delivery
            for delivery in await self.session.scalars(
                select(BackupDelivery).where(BackupDelivery.run_id == run.id)
            )
        }
        current_time = self.now()
        for admin_id, delivery in deliveries.items():
            if admin_id not in selected_ids and delivery.status is not DeliveryStatus.SUCCEEDED:
                delivery.status = DeliveryStatus.SKIPPED
                delivery.next_attempt_at = None
                delivery.last_error = "assignment revoked"
        created_recipients: list[dict[str, Any]] = []
        for admin_id in selected_ids:
            if admin_id not in deliveries:
                delivery = BackupDelivery(
                    run_id=run.id,
                    admin_id=admin_id,
                    status=DeliveryStatus.PENDING,
                    attempts=0,
                    next_attempt_at=current_time,
                )
                self.session.add(delivery)
                deliveries[admin_id] = delivery
                created_recipients.append(
                    {"admin_id": admin_id, "reason": recipient_reasons[admin_id]}
                )
        await self.session.commit()
        log.info(
            "backup_deliveries_created",
            run_id=run.id,
            profile_id=run.profile_id,
            recipients=created_recipients,
        )
        await self._cancel_boundary(run)
        run.stage = RunStage.TELEGRAM_DELIVERY
        reusable_media: dict[int, Any] = {}
        multipart = len(artifacts) > 1
        for admin_id in sorted(selected_ids):
            await self._cancel_boundary(run)
            delivery = deliveries[admin_id]
            if delivery.status is not DeliveryStatus.PENDING:
                continue
            current_time = self.now()
            if not self._is_due(delivery, current_time):
                continue
            if delivery.part_attempts >= self.max_attempts:
                delivery.status = DeliveryStatus.FAILED
                delivery.next_attempt_at = None
                delivery.last_error = "PERMANENT:MAX_ATTEMPTS"
                await self.session.commit()
                log.warning(
                    "telegram_delivery_attempt_failed",
                    run_id=run.id,
                    profile_id=run.profile_id,
                    delivery_id=delivery.id,
                    admin_id=admin_id,
                    telegram_id=None,
                    status=delivery.status.value,
                    attempt=delivery.attempts,
                    error_kind=DeliveryErrorKind.PERMANENT.value,
                    exception_class="MaxAttemptsExceeded",
                    telegram_response_error="maximum delivery attempts reached",
                    next_retry_time=None,
                    retry_pending=False,
                )
                continue
            user = await self.session.get(User, admin_id)
            authorized = None
            if (
                user is not None
                and user.role is Role.SUPERADMIN
                and user.enabled
                and user.deleted_at is None
            ):
                authorized = user
            elif user is not None and user.role is Role.ADMIN:
                authorized = await AccessRepository(self.session).authorized_recipient(
                    user.telegram_user_id, run.profile_id
                )
            if authorized is None:
                delivery.status = DeliveryStatus.SKIPPED
                delivery.next_attempt_at = None
                delivery.last_error = "recipient authorization revoked"
                await self.session.commit()
                log.warning(
                    "telegram_delivery_recipient_skipped",
                    run_id=run.id,
                    profile_id=run.profile_id,
                    admin_id=admin_id,
                    telegram_id=user.telegram_user_id if user is not None else None,
                    status=delivery.status.value,
                    error=delivery.last_error,
                )
                continue

            for artifact in artifacts[delivery.next_part - 1 :]:
                await self._cancel_boundary(run)
                last_report = 0.0

                def progress(_sent: int, _total: int) -> None:
                    nonlocal last_report
                    monotonic_now = time.monotonic()
                    if monotonic_now - last_report >= self.progress_interval:
                        last_report = monotonic_now

                current_time = self.now()
                delivery.attempts += 1
                delivery.part_attempts += 1
                delivery.next_attempt_at = current_time + timedelta(
                    seconds=self._retry_delay(delivery.part_attempts)
                )
                await self.session.commit()
                log.info(
                    "telegram_delivery_attempt_started",
                    run_id=run.id,
                    profile_id=run.profile_id,
                    delivery_id=delivery.id,
                    admin_id=admin_id,
                    telegram_id=authorized.telegram_user_id,
                    status=delivery.status.value,
                    attempt=delivery.attempts,
                    part=artifact.number,
                    total_parts=len(artifacts),
                )
                caption = build_backup_caption(run, profile, self.now(), self.timezone)
                if multipart:
                    caption = TelegramHtml(
                        f"{caption}\n📄 <b>Part {artifact.number}/{len(artifacts)}</b>"
                    )
                try:
                    async with asyncio.timeout(self.upload_timeout):
                        message = await self.transport.send_file(
                            authorized.telegram_user_id,
                            reusable_media.get(artifact.number) or str(artifact.path),
                            force_document=True,
                            progress_callback=progress,
                            caption=caption,
                            parse_mode="html",
                        )
                except Exception as exc:
                    kind, flood_wait = classify_delivery_error(exc)
                    delivery.last_error = f"{kind.value}:{type(exc).__name__}"
                    if (
                        kind is DeliveryErrorKind.PERMANENT
                        or delivery.part_attempts >= self.max_attempts
                    ):
                        delivery.status = DeliveryStatus.FAILED
                        delivery.next_attempt_at = None
                    else:
                        delivery.next_attempt_at = self.now() + timedelta(
                            seconds=self._retry_delay(delivery.part_attempts, flood_wait)
                        )
                    await self.session.commit()
                    log.warning(
                        "telegram_delivery_attempt_failed",
                        run_id=run.id,
                        profile_id=run.profile_id,
                        delivery_id=delivery.id,
                        admin_id=admin_id,
                        telegram_id=authorized.telegram_user_id,
                        status=delivery.status.value,
                        attempt=delivery.attempts,
                        part=artifact.number,
                        total_parts=len(artifacts),
                        error_kind=kind.value,
                        exception_class=type(exc).__name__,
                        telegram_response_error=" ".join(str(exc).split())[:2000],
                        next_retry_time=(
                            delivery.next_attempt_at.isoformat()
                            if delivery.next_attempt_at is not None
                            else None
                        ),
                        retry_pending=delivery.status is DeliveryStatus.PENDING,
                    )
                    break

                delivery.next_part = artifact.number + 1
                delivery.part_attempts = 0
                delivery.telegram_message_id = getattr(message, "id", None)
                media = getattr(message, "media", None)
                document = getattr(media, "document", None)
                if media is not None:
                    reusable_media[artifact.number] = media
                file_id = getattr(document, "id", None)
                delivery.telegram_file_id = str(file_id) if file_id is not None else None
                delivery.last_error = None
                delivery.next_attempt_at = self.now()
                await self.session.commit()

            if delivery.next_part > len(artifacts):
                delivery.status = DeliveryStatus.SUCCEEDED
                delivery.next_attempt_at = None
                delivery.delivered_at = self.now()
                await self.session.commit()
                log.info(
                    "telegram_delivery_attempt_succeeded",
                    run_id=run.id,
                    profile_id=run.profile_id,
                    delivery_id=delivery.id,
                    admin_id=admin_id,
                    telegram_id=authorized.telegram_user_id,
                    status=delivery.status.value,
                    attempt=delivery.attempts,
                    telegram_message_id=delivery.telegram_message_id,
                    total_parts=len(artifacts),
                )

        await self._cancel_boundary(run)
        pending = any(delivery.status is DeliveryStatus.PENDING for delivery in deliveries.values())
        failed = any(delivery.status is DeliveryStatus.FAILED for delivery in deliveries.values())
        if pending:
            run.status = RunStatus.DELIVERY_PENDING
            run.stage = RunStage.PENDING_DELIVERY
            run.lease_owner = None
            run.lease_expires_at = None
            await self.session.commit()
            return False
        if failed:
            await self._finish(run, RunStatus.FAILED, "DELIVERY_ACTION_REQUIRED")
            return False
        await self._finish(run, RunStatus.SUCCEEDED, None)
        await asyncio.to_thread(
            remove_owned_directory, self.pending_root, Path(run.pending_directory), run.uuid
        )
        run.pending_directory = None
        run.stage = RunStage.COMPLETE
        await self.session.commit()
        return True

    async def _finish(self, run: BackupRun, status: RunStatus, error_code: str | None) -> None:
        finished_at = self.now()
        expected_owner = run.lease_owner
        completed = (
            await self.session.execute(
                update(BackupRun)
                .where(
                    BackupRun.id == run.id,
                    BackupRun.status == RunStatus.DELIVERY_PENDING,
                    BackupRun.cancel_requested.is_(False),
                    BackupRun.lease_owner == expected_owner,
                )
                .values(
                    status=status,
                    stage=RunStage.COMPLETE if status is RunStatus.FAILED else RunStage.CLEANUP,
                    finished_at=finished_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=error_code,
                    error_message=(
                        "automatic Telegram delivery stopped; operator action required"
                        if error_code
                        else None
                    ),
                    failed_artifact_cleanup_at=(
                        finished_at + timedelta(seconds=self.failed_artifact_retention_seconds)
                        if status is RunStatus.FAILED
                        else None
                    ),
                    updated_at=finished_at,
                )
                .returning(BackupRun.id)
            )
        ).first()
        if completed is None:
            await self.session.rollback()
            await self._cancel_boundary(run)
            raise RuntimeError("run lease ownership lost")
        await record_run_transition(
            self.session,
            "failed" if status is RunStatus.FAILED else "succeeded",
            run.id,
            status=status.value,
            stage=(RunStage.COMPLETE if status is RunStatus.FAILED else RunStage.CLEANUP).value,
            error_code=error_code,
        )
        await self.session.commit()
        await self.session.refresh(run)
