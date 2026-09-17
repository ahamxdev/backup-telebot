from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text

from application.health_service import collect_operational_health, touch_heartbeats
from config.settings import Settings
from database.models import (
    AdminBackupAccess,
    AuditEvent,
    BackupArtifact,
    BackupDelivery,
    BackupPath,
    BackupProfile,
    BackupRun,
    FilesSource,
    MSSQLArtifactCleanup,
    RemoteServer,
    User,
)
from database.session import Database
from database.transfer import transfer_sqlite_to_postgresql
from domain.enums import (
    ArtifactKind,
    CompressionFormat,
    DeliveryStatus,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    ScheduleKind,
    SourceLocation,
    SourceType,
)
from pipeline.jobs import RunService

POSTGRES_IMAGE = (
    "postgres:15-bookworm@sha256:5d1d70e254e3c5d7d76847a9deebb18478cd518df37abf6b278d4bdb1fe5d96c"
)
PASSWORD = "disposable-control-database-password"
DOCKER = Path(shutil.which("docker") or "/usr/bin/docker")


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - controlled integration-test arguments
        [str(DOCKER), *args], check=False, capture_output=True, text=True, timeout=180
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).replace(PASSWORD, "[REDACTED]")
        raise AssertionError(detail[-4000:])
    return result


def alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"APP_ENV": "test", "DATABASE_URL": url})
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module
        [sys.executable, "-m", "alembic", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
    )
    assert result.returncode == 0, result.stderr.replace(PASSWORD, "[REDACTED]")
    return result


def wait_for_postgres(container: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = docker(
            "exec",
            container,
            "pg_isready",
            "-U",
            "control",
            "-d",
            "control",
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise AssertionError("PostgreSQL integration container did not become ready")


async def seed_sqlite(url: str) -> datetime:
    fixed = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
    database = Database(url)
    try:
        async with database.session_factory() as session:
            owner = User(
                id=10,
                telegram_user_id=10010,
                display_name="owner",
                role=Role.SUPERADMIN,
                created_at=fixed,
                updated_at=fixed,
            )
            admin = User(
                id=20,
                telegram_user_id=10020,
                display_name="admin",
                role=Role.ADMIN,
                created_by=10,
                created_at=fixed,
                updated_at=fixed,
            )
            server = RemoteServer(
                id=30,
                name="source-host",
                host="source.internal",
                ssh_port=22,
                ssh_username="backup",
                encrypted_private_key=b"encrypted-key",
                host_key_fingerprint="SHA256:test",
                created_at=fixed,
                updated_at=fixed,
            )
            profile = BackupProfile(
                id=40,
                uuid="00000000-0000-4000-8000-000000000040",
                name="migrated-profile",
                source_type=SourceType.FILES_DIRECTORIES,
                enabled=True,
                status=ProfileStatus.DRAFT,
                schedule_kind=ScheduleKind.MANUAL,
                compression_format=CompressionFormat.NONE,
                compression_preset=None,
                encryption_mode=EncryptionMode.NONE,
                created_by=10,
                created_at=fixed,
                updated_at=fixed,
            )
            session.add_all((owner, admin))
            await session.flush()
            session.add_all((server, profile))
            await session.flush()
            source = FilesSource(
                profile_id=40, location=SourceLocation.REMOTE_SERVER, remote_server_id=30
            )
            session.add_all(
                (
                    source,
                    AdminBackupAccess(
                        admin_id=20,
                        backup_profile_id=40,
                        created_by=10,
                        created_at=fixed,
                    ),
                    AuditEvent(
                        id=50,
                        event_type="migration-fixture",
                        actor_user_id=10,
                        target_type="backup_profile",
                        target_id="40",
                        details='{"safe":true}',
                        created_at=fixed,
                    ),
                )
            )
            await session.flush()
            session.add(BackupPath(id=60, files_source_id=40, path="/srv/backups/data"))
            run = BackupRun(
                id=70,
                uuid="00000000-0000-4000-8000-000000000070",
                profile_id=40,
                trigger=RunTrigger.MANUAL,
                status=RunStatus.QUEUED,
                stage=RunStage.QUEUED,
                created_at=fixed,
                updated_at=fixed,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                (
                    BackupArtifact(
                        id=80,
                        run_id=70,
                        kind=ArtifactKind.FINAL,
                        relative_path="artifact.tar",
                        size_bytes=10,
                        sha256="a" * 64,
                        created_at=fixed,
                        updated_at=fixed,
                    ),
                    BackupDelivery(
                        id=90,
                        run_id=70,
                        admin_id=20,
                        status=DeliveryStatus.PENDING,
                        attempts=0,
                        created_at=fixed,
                        updated_at=fixed,
                    ),
                    MSSQLArtifactCleanup(
                        id=100,
                        run_id=70,
                        operation_id="1" * 32,
                        artifact_path="/var/opt/mssql/backup/test.bak",
                        remote_server_id=30,
                        created_at=fixed,
                        updated_at=fixed,
                    ),
                )
            )
            await session.commit()
    finally:
        await database.dispose()
    return fixed


async def exercise_postgresql(sqlite_url: str, postgres_url: str, tmp_path: Path) -> None:
    fixed = await seed_sqlite(sqlite_url)
    source_path = Path(sqlite_url.removeprefix("sqlite+aiosqlite:///"))
    backup = tmp_path / "control.pre-postgresql.sqlite3"
    report = await transfer_sqlite_to_postgresql(source_path, postgres_url, backup_path=backup)
    assert report.source_counts == report.destination_counts
    assert report.source_counts["users"] == 2
    assert report.source_counts["backup_runs"] == 1
    assert len(report.table_sha256) == len(report.source_counts)
    assert backup.is_file() and stat.S_IMODE(backup.stat().st_mode) == 0o600

    database = Database(postgres_url)
    try:
        async with database.session_factory() as session:
            run = await session.get(BackupRun, 70)
            assert run is not None and run.created_at == fixed
            await session.refresh(run, ("artifacts", "deliveries", "profile"))
            assert run.profile.id == 40
            assert run.artifacts[0].id == 80
            assert run.deliveries[0].admin_id == 20
            cleanup = await session.get(MSSQLArtifactCleanup, 100)
            assert cleanup is not None and cleanup.remote_server_id == 30

        async with database.session_factory() as session:
            owner = await session.get(User, 10)
            assert owner is not None
            second = BackupProfile(
                name="postgres-run",
                source_type=SourceType.FILES_DIRECTORIES,
                enabled=True,
                status=ProfileStatus.DRAFT,
                schedule_kind=ScheduleKind.MANUAL,
                compression_format=CompressionFormat.NONE,
                compression_preset=None,
                encryption_mode=EncryptionMode.NONE,
                created_by=owner.id,
            )
            session.add(second)
            await session.commit()
            created = await RunService(session).enqueue_manual(owner, second.id)
            assert created.created

        async def claim(owner: str) -> BackupRun | None:
            async with database.session_factory() as session:
                return await RunService(session).claim(owner, 120, (RunStatus.QUEUED,))

        first, second_claim = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        claimed = [item for item in (first, second_claim) if item is not None]
        assert len(claimed) == 2
        assert {item.id for item in claimed} == {70, created.run.id}

        now = datetime.now(UTC)

        async def heartbeat(component: str) -> None:
            async with database.session_factory() as session:
                await touch_heartbeats(session, (component,), now=now)

        await asyncio.gather(heartbeat("telegram_bot"), heartbeat("telegram_worker"))
        async with database.session_factory() as session:
            await touch_heartbeats(
                session, ("worker", "scheduler"), activity=("scheduler",), now=now
            )
            settings = Settings(
                APP_ENV="test",
                DATABASE_URL=postgres_url,
                TELEGRAM_API_ID=1,
                TELEGRAM_API_HASH="hash",
                TELEGRAM_BOT_TOKEN="1:token",
                TELEGRAM_SESSION_PATH=tmp_path / "bot.session",
                TELEGRAM_WORKER_SESSION_PATH=tmp_path / "worker.session",
                DATA_DIR=tmp_path / "data",
                WORK_ROOT=tmp_path / "work",
                PENDING_DELIVERY_ROOT=tmp_path / "pending",
                MIN_FREE_DISK_BYTES=0,
            )
            for root in (settings.DATA_DIR, settings.WORK_ROOT, settings.PENDING_DELIVERY_ROOT):
                root.mkdir(mode=0o700)
            health = await collect_operational_health(session, settings, now=now)
            assert health.database_backend == "postgresql"
            assert health.ready and health.database

            max_user_id = await session.scalar(select(User.id).order_by(User.id.desc()).limit(1))
            added = User(telegram_user_id=10030, display_name="sequence-check", role=Role.ADMIN)
            session.add(added)
            await session.commit()
            assert added.id > int(max_user_id or 0)
            assert await session.scalar(text("SELECT count(*) FROM users")) == 3
    finally:
        await database.dispose()


def test_postgresql_migration_transfer_concurrency_and_health(tmp_path: Path) -> None:
    if shutil.which("docker") is None or docker("info", check=False).returncode:
        pytest.skip("Docker daemon is unavailable")
    container = f"backup-telebot-control-pg-{uuid.uuid4().hex[:12]}"
    try:
        docker(
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_DB=control",
            "--env",
            "POSTGRES_USER=control",
            "--env",
            f"POSTGRES_PASSWORD={PASSWORD}",
            POSTGRES_IMAGE,
        )
        wait_for_postgres(container)
        port = docker("port", container, "5432/tcp").stdout.strip().rsplit(":", 1)[-1]
        postgres_url = f"postgresql+asyncpg://control:{PASSWORD}@127.0.0.1:{port}/control"
        sqlite_path = tmp_path / "control.sqlite3"
        sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"
        alembic(postgres_url, "upgrade", "head")
        alembic(sqlite_url, "upgrade", "head")
        asyncio.run(exercise_postgresql(sqlite_url, postgres_url, tmp_path))
        result = alembic(postgres_url, "check")
        assert "No new upgrade operations detected" in result.stdout
    finally:
        docker("rm", "--force", container, check=False)
