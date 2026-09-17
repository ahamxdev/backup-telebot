from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, func, select, text

from application.health_service import migration_head
from database.models import Base
from database.session import create_engine


@dataclass(frozen=True)
class DatabaseTransferReport:
    backup_path: Path
    backup_sha256: str
    source_counts: dict[str, int]
    destination_counts: dict[str, int]
    table_sha256: dict[str, str]


def snapshot_sqlite_database(source: Path, destination: Path) -> str:
    """Create a consistent, private SQLite backup and return its SHA-256 digest."""
    if not source.is_file():
        raise FileNotFoundError(f"SQLite control database not found: {source}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to replace database backup: {destination}")
    source_uri = f"file:{source.resolve()}?mode=ro"
    try:
        with (
            sqlite3.connect(source_uri, uri=True) as origin,
            sqlite3.connect(destination) as backup,
        ):
            origin.backup(backup)
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RuntimeError("SQLite backup integrity check failed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    os.chmod(destination, 0o600)
    with destination.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return digest


def default_backup_path(source: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return source.with_name(f"{source.name}.pre-postgresql-{timestamp}.bak")


def _normalize_row(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    table = Base.metadata.tables[table_name]
    for column in table.columns:
        value = row.get(column.name)
        if (
            value is not None
            and isinstance(column.type, DateTime)
            and column.type.timezone
            and isinstance(value, datetime)
            and value.tzinfo is None
        ):
            row[column.name] = value.replace(tzinfo=UTC)
    return row


def _content_digest(rows: list[dict[str, Any]]) -> str:
    def canonical(value: Any) -> Any:
        if isinstance(value, datetime):
            aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return aware.isoformat()
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, Enum):
            return value.value
        return value

    payload = [[canonical(value) for value in row.values()] for row in rows]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def transfer_sqlite_to_postgresql(
    source: Path,
    destination_url: str,
    *,
    backup_path: Path | None = None,
) -> DatabaseTransferReport:
    """Snapshot SQLite and atomically copy every application table to empty PostgreSQL."""
    if not destination_url.startswith("postgresql+asyncpg://"):
        raise ValueError("destination must use postgresql+asyncpg")
    backup = backup_path or default_backup_path(source)
    digest = snapshot_sqlite_database(source, backup)
    source_url = f"sqlite+aiosqlite:///{backup.resolve()}"
    source_engine = create_engine(source_url)
    destination_engine = create_engine(destination_url)
    tables = list(Base.metadata.sorted_tables)
    source_counts: dict[str, int] = {}
    destination_counts: dict[str, int] = {}
    table_sha256: dict[str, str] = {}
    try:
        async with source_engine.connect() as source_connection:
            source_revision = str(
                await source_connection.scalar(text("SELECT version_num FROM alembic_version"))
                or "none"
            )
            if source_revision != migration_head():
                raise RuntimeError(
                    f"SQLite source revision {source_revision} is not head {migration_head()}"
                )
            rows_by_table: dict[str, list[dict[str, Any]]] = {}
            for table in tables:
                primary_key = list(table.primary_key.columns)
                statement = select(table)
                if primary_key:
                    statement = statement.order_by(*primary_key)
                rows = [
                    _normalize_row(table.name, dict(row))
                    for row in (await source_connection.execute(statement)).mappings()
                ]
                rows_by_table[table.name] = rows
                source_counts[table.name] = len(rows)

        async with destination_engine.begin() as destination:
            destination_revision = str(
                await destination.scalar(text("SELECT version_num FROM alembic_version")) or "none"
            )
            if destination_revision != migration_head():
                raise RuntimeError(
                    f"PostgreSQL destination revision {destination_revision} is not head "
                    f"{migration_head()}"
                )
            for table in tables:
                count = int(await destination.scalar(select(func.count()).select_from(table)) or 0)
                allowed = 1 if table.name == "system_settings" else 0
                if count > allowed:
                    raise RuntimeError(
                        f"PostgreSQL destination table {table.name} is not empty ({count} rows)"
                    )
            system_settings = Base.metadata.tables["system_settings"]
            await destination.execute(system_settings.delete())
            for table in tables:
                rows = rows_by_table[table.name]
                if rows:
                    await destination.execute(table.insert(), rows)
                primary_key = list(table.primary_key.columns)
                statement = select(table)
                if primary_key:
                    statement = statement.order_by(*primary_key)
                destination_rows = [
                    _normalize_row(table.name, dict(row))
                    for row in (await destination.execute(statement)).mappings()
                ]
                destination_counts[table.name] = len(destination_rows)
                if destination_counts[table.name] != source_counts[table.name]:
                    raise RuntimeError(f"row-count mismatch for {table.name}")
                source_digest = _content_digest(rows)
                if _content_digest(destination_rows) != source_digest:
                    raise RuntimeError(f"content mismatch for {table.name}")
                table_sha256[table.name] = source_digest
                if "id" in table.c and table.c.id.primary_key:
                    maximum = max((int(row["id"]) for row in rows), default=0)
                    await destination.execute(
                        text(
                            "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                            ":sequence_value, :is_called)"
                        ),
                        {
                            "table_name": table.name,
                            "sequence_value": max(1, maximum),
                            "is_called": maximum > 0,
                        },
                    )
    finally:
        await source_engine.dispose()
        await destination_engine.dispose()
    if source_counts != destination_counts:
        raise RuntimeError("database transfer row-count reconciliation failed")
    return DatabaseTransferReport(backup, digest, source_counts, destination_counts, table_sha256)
