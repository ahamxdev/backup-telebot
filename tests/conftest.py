from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from database.models import Base
from database.session import Database


@pytest.fixture(autouse=True)
def isolate_tests_from_local_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep live credentials and runtime settings out of test configuration."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}",
        DATA_DIR=tmp_path / "data",
        WORK_ROOT=tmp_path / "work",
        PENDING_DELIVERY_ROOT=tmp_path / "pending-delivery",
        TELEGRAM_SESSION_PATH=tmp_path / "telegram" / "bot.session",
        TELEGRAM_WORKER_SESSION_PATH=tmp_path / "telegram" / "worker.session",
        SUPERADMIN_TELEGRAM_IDS="1001",
        TELEGRAM_API_ID=12345,
        TELEGRAM_API_HASH="test-api-hash",
        TELEGRAM_BOT_TOKEN="123:test-token",
    )


@pytest_asyncio.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings.DATABASE_URL)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session_factory() as value:
        yield value
