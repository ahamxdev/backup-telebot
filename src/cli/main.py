import asyncio
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import text

from application.bootstrap_service import BootstrapService
from application.health_service import OperationalHealth, collect_operational_health
from config.settings import Settings
from database.session import Database
from database.transfer import transfer_sqlite_to_postgresql
from infrastructure.telegram import session_permissions_secure
from version import __version__

app = typer.Typer(help="Local administration for backup-telebot.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print application version."""
    typer.echo(__version__)


async def _bootstrap(settings: Settings) -> list[tuple[int, bool]]:
    superadmin_ids = settings.superadmin_telegram_ids()
    if not superadmin_ids:
        raise ValueError("SUPERADMIN_TELEGRAM_IDS is required")
    database = Database(settings.DATABASE_URL)
    try:
        async with database.session_factory() as session:
            service = BootstrapService(session)
            results: list[tuple[int, bool]] = []
            for telegram_id in superadmin_ids:
                user, created = await service.bootstrap_superadmin(
                    telegram_id, settings.TELEGRAM_SUPERADMIN_LANGUAGE
                )
                results.append((user.telegram_user_id, created))
            return results
    finally:
        await database.dispose()


@app.command("bootstrap-superadmin")
def bootstrap_superadmin() -> None:
    """Create any configured superadmins that do not exist."""
    try:
        results = asyncio.run(_bootstrap(Settings()))
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    for telegram_id, created in results:
        typer.echo(
            f"Superadmin {telegram_id}: {'created' if created else 'already exists; unchanged'}"
        )


@app.command("migrate-sqlite-to-postgres")
def migrate_sqlite_to_postgres(
    source: Annotated[
        Path,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Existing SQLite control database.",
        ),
    ] = Path(".runtime/data/app.sqlite3"),
) -> None:
    """Snapshot and atomically import the SQLite control database into PostgreSQL."""
    try:
        settings = Settings()
        report = asyncio.run(transfer_sqlite_to_postgresql(source, settings.DATABASE_URL))
    except Exception as exc:
        typer.echo(f"ERROR: migration failed ({type(exc).__name__})", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"backup={report.backup_path} sha256={report.backup_sha256}")
    typer.echo(f"source_counts={json.dumps(report.source_counts, sort_keys=True)}")
    typer.echo(f"destination_counts={json.dumps(report.destination_counts, sort_keys=True)}")
    typer.echo(f"table_sha256={json.dumps(report.table_sha256, sort_keys=True)}")


async def _database_check(settings: Settings) -> tuple[bool, str]:
    database = Database(settings.DATABASE_URL)
    try:
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
            revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            return True, str(revision or "no revision")
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await database.dispose()


@app.command()
def doctor() -> None:
    """Run safe diagnostics without printing secrets."""
    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"❌ configuration: {type(exc).__name__}")
        raise typer.Exit(1) from exc
    checks: list[tuple[bool, str]] = []
    telegram_ok = all(
        (settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH, settings.TELEGRAM_BOT_TOKEN)
    )
    checks.append((bool(telegram_ok), "Telegram bot settings"))
    checks.append(
        (
            not settings.TELEGRAM_PROXY_ENABLED
            or bool(settings.TELEGRAM_PROXY_HOST and settings.TELEGRAM_PROXY_PORT),
            "SOCKS5 configuration",
        )
    )
    for path in (settings.DATA_DIR, settings.TELEGRAM_SESSION_PATH.parent):
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            checks.append((os.access(path, os.W_OK), f"directory {path}"))
        except OSError:
            checks.append((False, f"directory {path}"))
    checks.append(
        (session_permissions_secure(settings.TELEGRAM_SESSION_PATH), "session permissions (0600)")
    )
    db_ok, revision = asyncio.run(_database_check(settings))
    backend = settings.DATABASE_URL.split(":", 1)[0].replace("postgresql", "PostgreSQL")
    checks.append((db_ok, f"{backend} and migration ({revision})"))
    free = shutil.disk_usage(Path.cwd()).free
    checks.extend(
        [
            (free >= 100 * 1024 * 1024, f"disk free {free // 1_048_576} MB"),
            (sys.version_info >= (3, 12), f"Python {sys.version.split()[0]}"),
        ]
    )
    for package in ("telethon", "sqlalchemy", "aiosqlite", "asyncpg", "alembic"):
        try:
            checks.append((True, f"{package} {importlib.metadata.version(package)}"))
        except importlib.metadata.PackageNotFoundError:
            checks.append((False, f"{package} missing"))
    for passed, label in checks:
        typer.echo(f"{'✅' if passed else '❌'} {label}")
    if not all(passed for passed, _ in checks):
        raise typer.Exit(1)


@app.command()
def healthcheck() -> None:
    """Report liveness/readiness and exit nonzero unless the system is ready."""
    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"live=yes ready=no operational=failed issue={type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc

    async def check() -> OperationalHealth:
        database = Database(settings.DATABASE_URL)
        try:
            async with database.session_factory() as session:
                return await collect_operational_health(session, settings)
        finally:
            await database.dispose()

    try:
        for root in (settings.DATA_DIR, settings.WORK_ROOT, settings.PENDING_DELIVERY_ROOT):
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not os.access(root, os.W_OK):
                raise OSError("required runtime root is not writable")
        health = asyncio.run(check())
    except Exception as exc:
        typer.echo(f"live=yes ready=no operational=failed issue={type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"live=yes ready={'yes' if health.ready else 'no'} operational={health.status} "
        f"db={health.database_backend}:{'ok' if health.database else 'failed'} "
        f"schema={health.current_revision}/{health.head_revision} "
        f"worker={'ok' if health.worker_alive else 'stale'} "
        f"scheduler={'ok' if health.scheduler_active else 'stale'} "
        f"telegram={'ok' if health.telegram_ready else 'not-ready'} "
        f"pending_bytes={health.pending_delivery_bytes} failed={health.failed_runs} "
        f"stale_leases={health.stale_leases} retained={health.retained_failed_artifacts} "
        f"issues={','.join(health.issues) or 'none'}"
    )
    for tool in health.tools:
        typer.echo(
            f"tool={tool.name} status={'ok' if tool.available else 'missing'} "
            f"version={tool.version}"
        )
    if not health.ready:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
