from sqlalchemy.ext.asyncio import AsyncSession
from telethon import Button

from application.run_config import restore_run_config
from application.settings_service import SettingsService
from database.models import BackupRun, User
from domain.enums import (
    DeliveryStatus,
    RunStatus,
    SourceLocation,
)
from pipeline.jobs import RunService
from presentation.telegram.backup_profiles import BackupProfilesMixin
from presentation.telegram.constants import (
    DISPLAYABLE_RUN_ERROR_CODES,
    RUN_PAGE_SIZE,
)
from presentation.telegram.datetime_format import (
    format_user_datetime,
    format_user_timestamp,
    timezone_label,
)
from presentation.telegram.formatting import code, format_bytes_iec
from presentation.telegram.i18n import t
from presentation.telegram.keyboards import (
    backups_menu,
)
from presentation.telegram.profile_summary import file_source_path_lines


class RunActionsMixin(BackupProfilesMixin):
    @staticmethod
    def _run_sizes(run: BackupRun, unknown: str) -> tuple[str, str]:
        original = (
            format_bytes_iec(run.original_size_bytes)
            if run.original_size_bytes is not None
            else unknown
        )
        final = (
            format_bytes_iec(run.final_size_bytes) if run.final_size_bytes is not None else unknown
        )
        return original, final

    @staticmethod
    def _safe_run_error(run: BackupRun) -> tuple[str, str]:
        if run.error_code is None and run.error_message is None:
            return "—", "none"
        code = run.error_code if run.error_code in DISPLAYABLE_RUN_ERROR_CODES else None
        messages = {
            "CANCELLED": "run_error_cancelled",
            "PROCESS_TIMEOUT": "run_error_timeout",
            "DELIVERY_ACTION_REQUIRED": "run_error_delivery_action_required",
            "UPLOAD_LIMIT": "run_error_upload_limit",
            "PENDING_ARTIFACT_MISSING": "run_error_artifact_invalid",
            "PENDING_CHECKSUM_MISMATCH": "run_error_artifact_invalid",
        }
        return code or "—", messages.get(code or "", "run_error_generic")

    async def _run_card_buttons(self, user: User, run: BackupRun) -> list[list[Button]]:
        return [
            [
                Button.inline(
                    t(user.language_code, "run_details"),
                    await self.callbacks.issue(user.id, "run_detail", run.id),
                )
            ]
        ]

    async def _show_active_runs(self, session: AsyncSession, user: User) -> None:
        timezone = (await SettingsService(session).get()).backup_timezone
        runs = await RunService(session).list_active(user)
        if not runs:
            await self._send(user, "no_active_operations", buttons=backups_menu(user.language_code))
            return
        await self._send(user, "active_runs_title", count=len(runs))
        for run in runs:
            original_size, final_size = self._run_sizes(run, t(user.language_code, "size_unknown"))
            size_summary = (
                ""
                if run.original_size_bytes is None and run.final_size_bytes is None
                else "\n\n"
                + t(
                    user.language_code,
                    "run_size_summary",
                    original_size=original_size,
                    final_size=final_size,
                )
            )
            await self.transport.send_text(
                user.telegram_user_id,
                t(
                    user.language_code,
                    "run_list_card",
                    run_id=run.id,
                    profile=run.profile.name,
                    status=t(user.language_code, f"run_status_{run.status.value.lower()}"),
                    stage=t(user.language_code, f"run_stage_{run.stage.value.lower()}"),
                    created=format_user_datetime(run.created_at, timezone),
                    size_summary=size_summary,
                ),
                buttons=await self._run_card_buttons(user, run),
            )
        await self._send(user, "active_runs", buttons=backups_menu(user.language_code))

    async def _show_run_history(self, session: AsyncSession, user: User, page_number: int) -> None:
        timezone = (await SettingsService(session).get()).backup_timezone
        page = await RunService(session).history(user, page_number, RUN_PAGE_SIZE)
        if not page.items:
            await self._send(user, "no_run_history", buttons=backups_menu(user.language_code))
            return
        await self._send(
            user,
            "run_history_header",
            page=page.page + 1,
            pages=page.pages,
            total=page.total,
        )
        for run in page.items:
            original_size, final_size = self._run_sizes(run, t(user.language_code, "size_unknown"))
            size_summary = "\n\n" + t(
                user.language_code,
                "run_size_summary",
                original_size=original_size,
                final_size=final_size,
            )
            await self.transport.send_text(
                user.telegram_user_id,
                t(
                    user.language_code,
                    "run_list_card",
                    run_id=run.id,
                    profile=run.profile.name,
                    status=t(user.language_code, f"run_status_{run.status.value.lower()}"),
                    stage=t(user.language_code, f"run_stage_{run.stage.value.lower()}"),
                    created=format_user_datetime(run.created_at, timezone),
                    size_summary=size_summary,
                ),
                buttons=await self._run_card_buttons(user, run),
            )
        navigation: list[Button] = []
        if page.page > 0:
            navigation.append(
                Button.inline(
                    t(user.language_code, "previous_page"),
                    await self.callbacks.issue(user.id, "run_history_page", page.page - 1),
                )
            )
        if page.page + 1 < page.pages:
            navigation.append(
                Button.inline(
                    t(user.language_code, "next_page"),
                    await self.callbacks.issue(user.id, "run_history_page", page.page + 1),
                )
            )
        await self.transport.send_text(
            user.telegram_user_id,
            t(user.language_code, "run_history_footer"),
            buttons=[navigation] if navigation else backups_menu(user.language_code),
        )

    async def _show_run_details(self, session: AsyncSession, user: User, run_id: int) -> None:
        timezone = (await SettingsService(session).get()).backup_timezone
        try:
            run = await RunService(session).details(user, run_id)
        except ValueError:
            await self._send(user, "run_action_unavailable")
            return
        deliveries = {status: 0 for status in DeliveryStatus}
        for delivery in run.deliveries:
            deliveries[delivery.status] += 1
        error_code, error_key = self._safe_run_error(run)
        original_size, final_size = self._run_sizes(run, t(user.language_code, "size_unknown"))
        if run.pending_directory is not None:
            artifact = t(
                user.language_code,
                "run_artifact_retained",
                size=run.final_size_bytes or 0,
            )
        elif run.failed_artifact_cleaned_at is not None:
            artifact = t(user.language_code, "run_artifact_purged")
        elif run.final_size_bytes is not None:
            artifact = t(user.language_code, "run_artifact_delivered", size=run.final_size_bytes)
        else:
            artifact = t(user.language_code, "none")
        profile = (
            restore_run_config(run.configuration_snapshot)
            if run.configuration_snapshot is not None
            else run.profile
        )
        if run.configuration_snapshot is None:
            await session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
            if profile.files_source is not None:
                await session.refresh(profile.files_source, ("paths",))
        source_specific = t(user.language_code, "none")
        if profile.files_source is not None:
            paths = file_source_path_lines(
                user.language_code, (path.path for path in profile.files_source.paths)
            )
            location_key = (
                "source_location_local"
                if profile.files_source.location is SourceLocation.LOCAL
                else "source_location_remote"
            )
            source_specific = t(
                user.language_code,
                "file_source_details",
                location=t(user.language_code, location_key),
                paths=paths,
            )
        elif profile.database_source is not None:
            source_specific = t(
                user.language_code,
                "database_source_details",
                engine=self._engine_label(user.language_code, profile.database_source.engine),
            )
        elif profile.gitlab_source is not None:
            location_key = (
                "source_location_local"
                if profile.gitlab_source.location is SourceLocation.LOCAL
                else "source_location_remote"
            )
            source_specific = t(
                user.language_code,
                "gitlab_source_details",
                location=t(user.language_code, location_key),
                container=code(profile.gitlab_source.container_name),
                backup_path=code(profile.gitlab_source.backup_path),
            )
        source_details = t(
            user.language_code,
            "run_source_details",
            source=self._source_label(user.language_code, profile.source_type),
            details=source_specific,
        )
        buttons: list[Button] = []
        if run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.DELIVERY_PENDING}:
            if not run.cancel_requested:
                buttons.append(
                    Button.inline(
                        t(user.language_code, "cancel_run"),
                        await self.callbacks.issue(user.id, "run_cancel", run.id),
                    )
                )
        elif run.status is RunStatus.FAILED:
            retryable_delivery = run.pending_directory is not None and any(
                delivery.status is DeliveryStatus.FAILED for delivery in run.deliveries
            )
            retryable_run = run.pending_directory is None and not any(
                delivery.status is DeliveryStatus.SUCCEEDED for delivery in run.deliveries
            )
            if retryable_run or retryable_delivery:
                buttons.append(
                    Button.inline(
                        t(user.language_code, "retry_run"),
                        await self.callbacks.issue(user.id, "run_retry", run.id),
                    )
                )
            if run.pending_directory is not None:
                buttons.append(
                    Button.inline(
                        t(user.language_code, "purge_artifact"),
                        await self.callbacks.issue(user.id, "run_purge", run.id),
                    )
                )
        await self.transport.send_text(
            user.telegram_user_id,
            t(
                user.language_code,
                "run_detail_card",
                run_id=run.id,
                profile=run.profile.name,
                source_details=source_details,
                status=t(user.language_code, f"run_status_{run.status.value.lower()}"),
                stage=t(user.language_code, f"run_stage_{run.stage.value.lower()}"),
                trigger=t(user.language_code, f"run_trigger_{run.trigger.value.lower()}"),
                created=format_user_timestamp(run.created_at, timezone),
                scheduled=format_user_timestamp(run.scheduled_for, timezone),
                started=format_user_timestamp(run.started_at, timezone),
                finished=format_user_timestamp(run.finished_at, timezone),
                timezone=timezone_label(run.finished_at or run.created_at, timezone),
                error_code=error_code,
                error_message=t(user.language_code, error_key),
                artifact=artifact,
                original_size=original_size,
                final_size=final_size,
                pending=deliveries[DeliveryStatus.PENDING],
                succeeded=deliveries[DeliveryStatus.SUCCEEDED],
                failed=deliveries[DeliveryStatus.FAILED],
                skipped=deliveries[DeliveryStatus.SKIPPED],
            ),
            buttons=[buttons] if buttons else backups_menu(user.language_code),
        )

    async def _cancel_run(self, session: AsyncSession, user: User, run_id: int) -> None:
        try:
            await RunService(session).request_cancel(user, run_id)
        except ValueError:
            await self._send(user, "run_action_unavailable")
            return
        await self._send(user, "run_cancel_requested", run_id=run_id)
        await self._show_run_details(session, user, run_id)

    async def _retry_run(self, session: AsyncSession, user: User, run_id: int) -> None:
        try:
            result = await RunService(session).retry_failed(
                user, run_id, self.settings.PENDING_DELIVERY_ROOT
            )
        except (RuntimeError, ValueError):
            await self._send(user, "run_action_unavailable")
            return
        await self._send(
            user,
            "run_delivery_retry_requested" if result.resumed_delivery else "run_retry_queued",
            run_id=result.run.id,
        )
        await self._show_run_details(session, user, result.run.id)

    async def _purge_run_artifact(self, session: AsyncSession, user: User, run_id: int) -> None:
        try:
            await RunService(session).purge_failed_artifact(
                user, run_id, self.settings.PENDING_DELIVERY_ROOT
            )
        except (RuntimeError, ValueError):
            await self._send(user, "run_action_unavailable")
            return
        await self._send(user, "run_artifact_purge_complete", run_id=run_id)
        await self._show_run_details(session, user, run_id)

    async def _confirm_run_artifact_purge(
        self, session: AsyncSession, user: User, run_id: int
    ) -> None:
        try:
            run = await RunService(session).details(user, run_id)
        except ValueError:
            await self._send(user, "run_action_unavailable")
            return
        if run.status is not RunStatus.FAILED or run.pending_directory is None:
            await self._send(user, "run_action_unavailable")
            return
        await self.transport.send_text(
            user.telegram_user_id,
            t(user.language_code, "run_artifact_purge_confirm", run_id=run.id),
            buttons=[
                [
                    Button.inline(
                        t(user.language_code, "confirm_delete"),
                        await self.callbacks.issue(user.id, "run_purge_confirm", run.id),
                    )
                ]
            ],
        )
