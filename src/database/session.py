import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, pool_pre_ping=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA database_list")
            database_files = [row[2] for row in cursor.fetchall() if row[2]]
            cursor.close()
            for filename in database_files:
                database_path = Path(filename)
                for state_path in (
                    database_path,
                    Path(f"{database_path}-wal"),
                    Path(f"{database_path}-shm"),
                ):
                    if state_path.exists():
                        os.chmod(state_path, 0o600)

    return engine


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
