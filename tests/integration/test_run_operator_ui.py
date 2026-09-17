from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from application.services import AdminService, BackupService, BootstrapService
from database.models import (
    BackupArtifact,
    BackupDelivery,
    BackupRun,
)
from domain.enums import (
    ArtifactKind,
    DeliveryStatus,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
)
from infrastructure.telegram.transport import TelegramTransport
from pipeline.finalize import finalize_artifact
from presentation.telegram.dispatcher import TelegramDispatcher
from presentation.telegram.i18n import t


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, Any]] = []

    async def send_message(self, entity: int, message: str, **kwargs: Any) -> None:
        self.messages.append((entity, message, kwargs.get("buttons")))

    async def send_file(self, entity: int, file: str, **kwargs: Any) -> None:
        raise AssertionError("operator UI must not send backup files")


@dataclass
class Event:
    sender_id: int
    raw_text: str = ""
    data: bytes | None = None
    is_private: bool = True


async def _profile(session: Any, superadmin: Any, name: str = "Production") -> Any:
    return await BackupService(session).create_draft(
        superadmin, name, None, SourceType.FILES_DIRECTORIES
    )


async def _failed_delivery_run(
    session: Any,
    settings: Any,
    superadmin: Any,
) -> tuple[BackupRun, BackupDelivery, BackupDelivery, Path]:
    profile = await _profile(session, superadmin)
    succeeded_admin = await AdminService(session).create_admin(
        superadmin, 2002, "Succeeded", {profile.id}
    )
    failed_admin = await AdminService(session).create_admin(
        superadmin, 2003, "Failed", {profile.id}
    )
    run = BackupRun(
        uuid="00000000-0000-4000-8000-00000000a001",
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.FAILED,
        stage=RunStage.COMPLETE,
        finished_at=datetime(2026, 9, 12, 10, tzinfo=UTC),
        error_code="DELIVERY_ACTION_REQUIRED",
        error_message="raw exception password=must-not-appear /secret/path",
    )
    session.add(run)
    await session.flush()
    source = settings.WORK_ROOT / "artifact"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"verified-backup")
    finalized = finalize_artifact(
        source,
        settings.PENDING_DELIVERY_ROOT,
        profile.name,
        run.uuid,
        datetime(2026, 9, 12, 10, tzinfo=UTC),
        "UTC",
        "backup.tar",
        {"safe": True},
    )
    run.pending_directory = str(finalized.directory)
    run.final_filename = finalized.artifact.name
    run.final_size_bytes = finalized.size
    run.final_sha256 = finalized.checksum
    artifact = BackupArtifact(
        run_id=run.id,
        kind=ArtifactKind.FINAL,
        relative_path=finalized.artifact.name,
        size_bytes=finalized.size,
        sha256=finalized.checksum,
    )
    succeeded = BackupDelivery(
        run_id=run.id,
        admin_id=succeeded_admin.id,
        status=DeliveryStatus.SUCCEEDED,
        attempts=1,
    )
    failed = BackupDelivery(
        run_id=run.id,
        admin_id=failed_admin.id,
        status=DeliveryStatus.FAILED,
        attempts=10,
        last_error="PERMANENT:PasswordWasInRawException",
    )
    session.add_all([artifact, succeeded, failed])
    await session.commit()
    return run, succeeded, failed, finalized.directory


async def test_run_ui_rechecks_superadmin_and_callback_actor(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await _profile(session, superadmin)
        normal_admin = await AdminService(session).create_admin(
            superadmin, 2002, "Normal", {profile.id}
        )
        run = BackupRun(
            profile_id=profile.id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.QUEUED,
            stage=RunStage.QUEUED,
        )
        session.add(run)
        await session.commit()
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)

    await dispatcher.handle(
        Event(normal_admin.telegram_user_id, t(normal_admin.language_code, "active_runs"))
    )
    assert "not available" in client.messages[-1][1]
    token = await dispatcher.callbacks.issue(superadmin.id, "run_cancel", run.id)
    await dispatcher.handle(Event(normal_admin.telegram_user_id, data=token))

    async with database.session_factory() as session:
        stored = await session.get(BackupRun, run.id)
        assert stored is not None
        assert stored.status is RunStatus.QUEUED
        assert not stored.cancel_requested


async def test_run_history_is_paginated_with_actor_bound_tokens(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await _profile(session, superadmin)
        session.add_all(
            BackupRun(
                profile_id=profile.id,
                trigger=RunTrigger.MANUAL,
                status=RunStatus.SUCCEEDED,
                stage=RunStage.COMPLETE,
            )
            for _ in range(9)
        )
        await session.commit()
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)

    await dispatcher.handle(Event(1001, t(superadmin.language_code, "run_history")))

    assert "page 1/2" in client.messages[0][1]
    assert (
        len([message for _entity, message, _buttons in client.messages if "Run #" in message]) == 8
    )
    next_button = client.messages[-1][2][0][0]
    assert next_button.data.startswith(b"v1:")
    await dispatcher.handle(Event(1001, data=next_button.data))
    assert any("page 2/2" in message for _entity, message, _buttons in client.messages)


async def test_run_details_show_status_and_only_safe_error(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        run, _succeeded, _failed, _directory = await _failed_delivery_run(
            session, settings, superadmin
        )
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    token = await dispatcher.callbacks.issue(superadmin.id, "run_detail", run.id)

    await dispatcher.handle(Event(1001, data=token))

    message = client.messages[-1][1]
    assert "✅ <b>Status:</b> Failed" in message
    assert "⚙️ <b>Stage:</b> Complete" in message
    assert "▶️ <b>Trigger:</b> Manual" in message
    assert "DELIVERY_ACTION_REQUIRED" in message
    assert "requires operator action" in message
    assert "must-not-appear" not in message
    assert "/secret/path" not in message
    assert "PasswordWasInRawException" not in message
    assert "Succeeded: <code>1</code>" in message
    assert "Failed: <code>1</code>" in message


async def test_cancel_active_run_from_operator_ui(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await _profile(session, superadmin)
        run = BackupRun(
            profile_id=profile.id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.QUEUED,
            stage=RunStage.QUEUED,
        )
        session.add(run)
        await session.commit()
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    token = await dispatcher.callbacks.issue(superadmin.id, "run_cancel", run.id)

    await dispatcher.handle(Event(1001, data=token))

    async with database.session_factory() as session:
        stored = await session.get(BackupRun, run.id)
        assert stored is not None
        assert stored.status is RunStatus.CANCELLED
        assert stored.cancel_requested
    assert any(
        "Cancellation requested" in message for _entity, message, _buttons in client.messages
    )


async def test_retry_resumes_only_failed_recipients(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        run, succeeded, failed, directory = await _failed_delivery_run(
            session, settings, superadmin
        )
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    token = await dispatcher.callbacks.issue(superadmin.id, "run_retry", run.id)

    await dispatcher.handle(Event(1001, data=token))

    async with database.session_factory() as session:
        stored = await session.get(BackupRun, run.id)
        stored_succeeded = await session.get(BackupDelivery, succeeded.id)
        stored_failed = await session.get(BackupDelivery, failed.id)
        assert stored is not None and stored.status is RunStatus.DELIVERY_PENDING
        assert stored_succeeded is not None
        assert stored_succeeded.status is DeliveryStatus.SUCCEEDED
        assert stored_succeeded.attempts == 1
        assert stored_failed is not None
        assert stored_failed.status is DeliveryStatus.PENDING
        assert stored_failed.attempts == 0
        assert stored_failed.next_attempt_at is not None
    assert directory.exists()


async def test_retry_non_finalized_failure_creates_a_new_run(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        profile = await _profile(session, superadmin)
        profile.enabled = True
        failed_run = BackupRun(
            profile_id=profile.id,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.FAILED,
            stage=RunStage.COMPLETE,
            error_code="PIPELINE_FAILED",
            error_message="protected diagnostics",
        )
        session.add(failed_run)
        await session.commit()
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    token = await dispatcher.callbacks.issue(superadmin.id, "run_retry", failed_run.id)

    await dispatcher.handle(Event(1001, data=token))

    async with database.session_factory() as session:
        runs = list(
            await session.scalars(
                select(BackupRun).where(BackupRun.profile_id == profile.id).order_by(BackupRun.id)
            )
        )
        assert len(runs) == 2
        assert runs[0].status is RunStatus.FAILED
        assert runs[1].status is RunStatus.QUEUED
    assert any("new retry run" in message for _entity, message, _buttons in client.messages)


async def test_purge_removes_only_verified_artifact_and_keeps_metadata(database, settings) -> None:
    async with database.session_factory() as session:
        superadmin, _ = await BootstrapService(session).bootstrap_superadmin(1001)
        run, _succeeded, _failed, directory = await _failed_delivery_run(
            session, settings, superadmin
        )
    client = FakeClient()
    dispatcher = TelegramDispatcher(database.session_factory, TelegramTransport(client), settings)
    token = await dispatcher.callbacks.issue(superadmin.id, "run_purge", run.id)

    await dispatcher.handle(Event(1001, data=token))
    assert directory.exists()
    confirmation = client.messages[-1][2][0][0]
    await dispatcher.handle(Event(1001, data=confirmation.data))

    assert not directory.exists()
    async with database.session_factory() as session:
        stored = await session.get(BackupRun, run.id)
        artifact = await session.scalar(
            select(BackupArtifact).where(BackupArtifact.run_id == run.id)
        )
        assert stored is not None
        assert stored.status is RunStatus.FAILED
        assert stored.pending_directory is None
        assert stored.failed_artifact_cleaned_at is not None
        assert artifact is not None
        assert artifact.sha256 == run.final_sha256
