import asyncio
import socket
import uuid

import structlog

from application.health_service import (
    touch_heartbeats,
    validate_configured_tools,
    validate_runtime_schema,
)
from application.run_config import snapshot_legacy_active_runs
from application.worker_paths import publish_worker_paths
from bootstrap.local_source_validation import LocalSourceValidationServer
from config.settings import Settings
from database.models import SystemSettings, utc_now
from database.session import Database
from domain.enums import RunStatus
from infrastructure.telegram import TelegramUploadTransport
from infrastructure.telegram.errors import classify_delivery_error
from pipeline.jobs import RunService
from pipeline.recovery import (
    cleanup_expired_failed_artifacts,
    cleanup_orphaned_mssql_artifacts,
    recover_recorded_filesystem,
)
from pipeline.resources import disk_has_capacity
from pipeline.schedule import Scheduler
from pipeline.worker import PipelineRunner
from security import SecretCipher


async def run_worker(settings: Settings) -> None:
    settings.require_telegram()
    key = settings.MASTER_ENCRYPTION_KEY_B64
    if key is None:
        raise ValueError("worker requires MASTER_ENCRYPTION_KEY_B64")
    SecretCipher(key.get_secret_value())
    validation_database = Database(settings.DATABASE_URL)
    try:
        if settings.APP_ENV == "production":
            await validation_database.verify_postgresql_ready()
        async with validation_database.session_factory() as session:
            await validate_runtime_schema(session)
            await snapshot_legacy_active_runs(session)
            await validate_configured_tools(session, settings)
    finally:
        await validation_database.dispose()
    database = Database(settings.DATABASE_URL)
    owner = f"{socket.gethostname()}:{uuid.uuid4()}"
    upload_transport = TelegramUploadTransport.for_worker(settings)
    runner = PipelineRunner(database.session_factory, settings, upload_transport, owner)
    tasks: set[asyncio.Task[None]] = set()
    next_runtime_heartbeat = 0.0
    source_validation = LocalSourceValidationServer(settings)
    try:
        await source_validation.start()
        try:
            await upload_transport.prepare()
        except Exception as exc:
            structlog.get_logger().warning(
                "worker_telegram_startup_unavailable",
                exception_class=type(exc).__name__,
                error_kind=classify_delivery_error(exc)[0].value,
            )
        await cleanup_orphaned_mssql_artifacts(database.session_factory, settings)
        await recover_recorded_filesystem(database.session_factory, settings)
        while True:
            for completed in tuple(tasks):
                if completed.done():
                    tasks.remove(completed)
                    if not completed.cancelled() and completed.exception() is not None:
                        structlog.get_logger().error(
                            "worker_run_task_failed",
                            exception_class=type(completed.exception()).__name__,
                        )
            await cleanup_expired_failed_artifacts(database.session_factory, settings)
            async with database.session_factory() as session:
                stored = await session.get(SystemSettings, 1)
                if stored is None:
                    stored = SystemSettings(id=1)
                    session.add(stored)
                    await session.commit()
                service = RunService(session)
                capacity = disk_has_capacity(settings, await service.pending_bytes())
                monotonic_now = asyncio.get_running_loop().time()
                heartbeat_due = monotonic_now >= next_runtime_heartbeat
                if capacity:
                    await Scheduler(session).tick(utc_now(), stored.backup_timezone)
                    if heartbeat_due:
                        heartbeat_components: tuple[str, ...] = (
                            ("worker", "scheduler", "telegram_worker")
                            if upload_transport.is_connected()
                            else ("worker", "scheduler")
                        )
                        await touch_heartbeats(
                            session,
                            heartbeat_components,
                            activity=("scheduler",),
                        )
                elif heartbeat_due:
                    heartbeat_components = (
                        ("worker", "telegram_worker")
                        if upload_transport.is_connected()
                        else ("worker",)
                    )
                    await touch_heartbeats(session, heartbeat_components)
                if heartbeat_due:
                    try:
                        await asyncio.to_thread(publish_worker_paths, settings)
                    except OSError:
                        structlog.get_logger().warning("worker_source_paths_report_unavailable")
                    next_runtime_heartbeat = (
                        monotonic_now + settings.HEALTH_HEARTBEAT_INTERVAL_SECONDS
                    )
                while len(tasks) < settings.MAX_CONCURRENT_BACKUPS:
                    run = await service.claim_cancel_requested(owner, settings.JOB_LEASE_SECONDS)
                    if run is None:
                        statuses: tuple[RunStatus, ...] = (RunStatus.DELIVERY_PENDING,)
                        if capacity:
                            statuses += (RunStatus.QUEUED, RunStatus.RUNNING)
                        run = await service.claim(owner, settings.JOB_LEASE_SECONDS, statuses)
                    if run is None:
                        break
                    task = asyncio.create_task(runner.execute(run.id))
                    tasks.add(task)
            await asyncio.sleep(settings.WORKER_POLL_SECONDS)
    finally:
        await source_validation.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await upload_transport.close()
        finally:
            await database.dispose()


def entrypoint() -> None:
    from app_logging.setup import configure_logging
    from config.settings import get_settings

    settings = get_settings()
    configure_logging(settings)
    asyncio.run(run_worker(settings))
