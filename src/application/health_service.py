import asyncio
import importlib.metadata
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from database.models import (
    BackupProfile,
    BackupRun,
    RuntimeHeartbeat,
    utc_now,
)
from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    DatabaseEngine,
    EncryptionMode,
    RunStatus,
    ScheduleKind,
    SourceLocation,
    SourceType,
)
from infrastructure.telegram import session_permissions_secure


@dataclass(frozen=True)
class ToolHealth:
    name: str
    available: bool
    version: str


@dataclass(frozen=True)
class OperationalHealth:
    live: bool
    ready: bool
    status: str
    database: bool
    database_backend: str
    schema_current: bool
    current_revision: str
    head_revision: str
    worker_alive: bool
    scheduler_active: bool
    telegram_ready: bool
    free_disk_bytes: int
    pending_delivery_bytes: int
    queued_runs: int
    running_runs: int
    failed_runs: int
    stale_leases: int
    retained_failed_artifacts: int
    tools: tuple[ToolHealth, ...]
    issues: tuple[str, ...]
    used_disk_bytes: int = 0
    storage_ready: bool = False
    profile_count: int = 0
    pending_delivery_count: int = 0
    checked_at: datetime | None = None
    helper_configured: bool = False
    scheduled_profile_count: int = 0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def migration_head() -> str:
    project = Path(__file__).resolve().parents[2]
    config = Config(str(project / "alembic.ini"))
    config.set_main_option("script_location", str(project / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    return str(head or "none")


async def touch_heartbeats(
    session: AsyncSession,
    components: tuple[str, ...],
    *,
    activity: tuple[str, ...] = (),
    now: datetime | None = None,
) -> None:
    timestamp = now or utc_now()
    active = set(activity)
    for component in components:
        heartbeat = await session.get(RuntimeHeartbeat, component)
        if heartbeat is None:
            heartbeat = RuntimeHeartbeat(component=component, heartbeat_at=timestamp)
            session.add(heartbeat)
        else:
            heartbeat.heartbeat_at = timestamp
        if component in active:
            heartbeat.activity_at = timestamp
    await session.commit()


async def _command_version(name: str, executable: str, argument: str = "--version") -> ToolHealth:
    path = shutil.which(executable)
    if path is None:
        return ToolHealth(name, False, "missing")
    try:
        process = await asyncio.create_subprocess_exec(
            path,
            argument,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        return ToolHealth(name, False, "unavailable")
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return ToolHealth(name, False, "unavailable")
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    lines = stdout.decode("utf-8", "replace").splitlines()
    raw_version = " | ".join(" ".join(line.split()) for line in lines[:3] if line.strip())
    raw_version = raw_version or "version unavailable"
    version = "".join(character for character in raw_version if character.isprintable())[:160]
    return ToolHealth(name, process.returncode == 0, version)


def _package_version(name: str, package: str) -> ToolHealth:
    try:
        return ToolHealth(name, True, importlib.metadata.version(package)[:80])
    except importlib.metadata.PackageNotFoundError:
        return ToolHealth(name, False, "missing")


async def configured_tool_health(
    session: AsyncSession, settings: Settings
) -> tuple[ToolHealth, ...]:
    profiles = list(
        await session.scalars(
            select(BackupProfile).where(
                BackupProfile.enabled.is_(True), BackupProfile.archived_at.is_(None)
            )
        )
    )
    required: dict[str, tuple[str, str]] = {}
    packages: dict[str, str] = {}
    for profile in profiles:
        await session.refresh(profile, ("database_source", "files_source", "gitlab_source"))
        if profile.source_type is SourceType.DATABASE and profile.database_source is not None:
            engine = profile.database_source.engine
            if engine is DatabaseEngine.POSTGRESQL:
                binary = settings.POSTGRES_CLIENT_ROOT / "15" / "bin"
                required["pg_dump-15"] = (str(binary / "pg_dump"), "--version")
                required["pg_restore-15"] = (str(binary / "pg_restore"), "--version")
            elif engine is DatabaseEngine.MYSQL_MARIADB:
                dump_client = "mysqldump" if shutil.which("mysqldump") else "mariadb-dump"
                required["mysql/mariadb-dump"] = (dump_client, "--version")
            elif engine is DatabaseEngine.MSSQL:
                packages["python-tds"] = "python-tds"
                packages["pyOpenSSL"] = "pyOpenSSL"
        elif profile.files_source is not None:
            required["tar"] = ("tar", "--version")
            if profile.files_source.location is SourceLocation.REMOTE_SERVER:
                required["rsync"] = ("rsync", "--version")
                required["ssh"] = ("ssh", "-V")
                packages["asyncssh"] = "asyncssh"
        archive = {
            ArchiveFormat.TAR: ("tar", "tar"),
            ArchiveFormat.ZIP: ("zip", "zip"),
            ArchiveFormat.SEVEN_Z: ("7z", "7z"),
        }[profile.archive_format]
        archive_argument = "i" if archive[0] == "7z" else "--version"
        required[archive[0]] = (archive[1], archive_argument)
        compression = {
            CompressionAlgorithm.ZSTANDARD: ("zstd", "zstd"),
            CompressionAlgorithm.GZIP: ("gzip", "gzip"),
            CompressionAlgorithm.XZ: ("xz", "xz"),
            CompressionAlgorithm.BZIP2: ("bzip2", "bzip2"),
            CompressionAlgorithm.LZ4: ("lz4", "lz4"),
        }.get(profile.compression_algorithm)
        if compression:
            argument = "i" if compression[0] == "7z" else "--version"
            required[compression[0]] = (compression[1], argument)
        if profile.encryption_mode is not EncryptionMode.NONE:
            required["age"] = ("age", "--version")
    command_results = await asyncio.gather(
        *(_command_version(name, command, arg) for name, (command, arg) in sorted(required.items()))
    )
    return tuple(command_results) + tuple(
        _package_version(name, package) for name, package in sorted(packages.items())
    )


async def validate_runtime_schema(session: AsyncSession) -> None:
    try:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception:
        raise RuntimeError("control database schema is unavailable") from None
    if str(revision or "none") != migration_head():
        raise RuntimeError("control database migration is not at the current head")


async def validate_configured_tools(session: AsyncSession, settings: Settings) -> None:
    tools = await configured_tool_health(session, settings)
    missing = sorted(tool.name for tool in tools if not tool.available)
    if missing:
        raise RuntimeError(f"required backup tools unavailable: {', '.join(missing)}")


async def collect_operational_health(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> OperationalHealth:
    checked_at = now or utc_now()
    await session.execute(text("SELECT 1"))
    try:
        async with session.begin_nested():
            current_revision = str(
                await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
                or "none"
            )
    except SQLAlchemyError:
        current_revision = "none"
    head_revision = migration_head()
    schema_current = current_revision == head_revision
    heartbeats = {item.component: item for item in await session.scalars(select(RuntimeHeartbeat))}

    def fresh(component: str, seconds: int, *, use_activity: bool = False) -> bool:
        record = heartbeats.get(component)
        value = (
            record.activity_at
            if record is not None and use_activity
            else (record.heartbeat_at if record is not None else None)
        )
        return value is not None and _as_utc(value) >= checked_at - timedelta(seconds=seconds)

    worker_alive = fresh("worker", settings.HEALTH_WORKER_STALE_SECONDS)
    scheduled_profiles = int(
        await session.scalar(
            select(func.count(BackupProfile.id)).where(
                BackupProfile.enabled.is_(True),
                BackupProfile.archived_at.is_(None),
                BackupProfile.schedule_kind != ScheduleKind.MANUAL,
                or_(
                    BackupProfile.schedule_kind != ScheduleKind.ONCE,
                    and_(
                        BackupProfile.schedule_kind == ScheduleKind.ONCE,
                        BackupProfile.next_scheduled_at.is_not(None),
                    ),
                ),
            )
        )
        or 0
    )
    scheduler_active = scheduled_profiles == 0 or fresh(
        "scheduler", settings.HEALTH_SCHEDULER_STALE_SECONDS, use_activity=True
    )
    telegram_configured = all(
        (settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH, settings.TELEGRAM_BOT_TOKEN)
    )
    session_files_secure = session_permissions_secure(
        settings.TELEGRAM_SESSION_PATH
    ) and session_permissions_secure(settings.TELEGRAM_WORKER_SESSION_PATH)
    telegram_ready = bool(
        telegram_configured
        and session_files_secure
        and fresh("telegram_bot", settings.HEALTH_TELEGRAM_STALE_SECONDS)
        and fresh("telegram_worker", settings.HEALTH_TELEGRAM_STALE_SECONDS)
    )
    status_rows = (
        await session.execute(select(BackupRun.status, func.count()).group_by(BackupRun.status))
    ).all()
    status_counts: dict[RunStatus, int] = {status: int(count) for status, count in status_rows}
    stale_leases = int(
        await session.scalar(
            select(func.count(BackupRun.id)).where(
                BackupRun.status.in_(
                    (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.DELIVERY_PENDING)
                ),
                BackupRun.lease_owner.is_not(None),
                BackupRun.lease_expires_at < checked_at,
            )
        )
        or 0
    )
    retained = int(
        await session.scalar(
            select(func.count(BackupRun.id)).where(
                BackupRun.status == RunStatus.FAILED,
                BackupRun.pending_directory.is_not(None),
            )
        )
        or 0
    )
    pending_bytes = int(
        await session.scalar(
            select(func.coalesce(func.sum(BackupRun.final_size_bytes), 0)).where(
                BackupRun.pending_directory.is_not(None)
            )
        )
        or 0
    )
    try:
        disk_usage = shutil.disk_usage(settings.PENDING_DELIVERY_ROOT)
        free_disk = disk_usage.free
        used_disk = disk_usage.used
        roots_writable = all(
            root.exists() and os.access(root, os.W_OK)
            for root in (settings.DATA_DIR, settings.WORK_ROOT, settings.PENDING_DELIVERY_ROOT)
        )
    except OSError:
        free_disk = 0
        used_disk = 0
        roots_writable = False
    disk_ready = roots_writable and free_disk >= settings.MIN_FREE_DISK_BYTES
    tools = await configured_tool_health(session, settings)
    tools_ready = all(tool.available for tool in tools)
    issues: list[str] = []
    for passed, code in (
        (schema_current, "schema_outdated"),
        (worker_alive, "worker_stale"),
        (scheduler_active, "scheduler_stale"),
        (telegram_ready, "telegram_not_ready"),
        (disk_ready, "disk_capacity_low"),
        (pending_bytes < settings.MAX_PENDING_DELIVERY_BYTES, "pending_storage_limit"),
        (tools_ready, "configured_tool_missing"),
    ):
        if not passed:
            issues.append(code)
    failed_runs = int(status_counts.get(RunStatus.FAILED, 0))
    if stale_leases:
        issues.append("stale_leases")
    if retained:
        issues.append("retained_failed_artifacts")
    if failed_runs:
        issues.append("failed_runs")
    ready = all(
        (
            schema_current,
            worker_alive,
            scheduler_active,
            telegram_ready,
            disk_ready,
            pending_bytes < settings.MAX_PENDING_DELIVERY_BYTES,
            tools_ready,
        )
    )
    return OperationalHealth(
        live=True,
        ready=ready,
        status="healthy" if ready and not issues else ("degraded" if ready else "failed"),
        database=True,
        database_backend=session.bind.dialect.name if session.bind is not None else "unknown",
        schema_current=schema_current,
        current_revision=current_revision,
        head_revision=head_revision,
        worker_alive=worker_alive,
        scheduler_active=scheduler_active,
        telegram_ready=telegram_ready,
        free_disk_bytes=free_disk,
        pending_delivery_bytes=pending_bytes,
        queued_runs=int(status_counts.get(RunStatus.QUEUED, 0)),
        running_runs=int(status_counts.get(RunStatus.RUNNING, 0)),
        failed_runs=failed_runs,
        stale_leases=stale_leases,
        retained_failed_artifacts=retained,
        tools=tools,
        issues=tuple(issues),
        used_disk_bytes=used_disk,
        storage_ready=disk_ready,
        profile_count=int(
            await session.scalar(
                select(func.count(BackupProfile.id)).where(BackupProfile.archived_at.is_(None))
            )
            or 0
        ),
        pending_delivery_count=int(
            await session.scalar(
                select(func.count(BackupRun.id)).where(BackupRun.pending_directory.is_not(None))
            )
            or 0
        ),
        checked_at=checked_at,
        scheduled_profile_count=scheduled_profiles,
        helper_configured=bool(
            await session.scalar(
                select(BackupProfile.id)
                .where(
                    BackupProfile.archived_at.is_(None),
                    BackupProfile.enabled.is_(True),
                    BackupProfile.source_type == SourceType.GITLAB,
                    BackupProfile.gitlab_source.has(location=SourceLocation.LOCAL),
                )
                .limit(1)
            )
        ),
    )
