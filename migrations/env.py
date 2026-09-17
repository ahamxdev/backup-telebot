import asyncio
import os
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from config.settings import Settings
from database.migration_types import compare_sqlite_enum_storage
from database.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
settings = Settings()
settings.DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        compare_type=compare_sqlite_enum_storage,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=compare_sqlite_enum_storage,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        if connection.dialect.name == "sqlite":
            database_files = (await connection.exec_driver_sql("PRAGMA database_list")).all()
            for _sequence, _name, filename in database_files:
                if not filename:
                    continue
                path = Path(filename)
                for state_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                    if state_path.exists():
                        os.chmod(state_path, 0o600)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
