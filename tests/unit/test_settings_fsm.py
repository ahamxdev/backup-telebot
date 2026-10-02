from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from config.settings import Settings, parse_superadmin_telegram_ids
from infrastructure.telegram.transport import proxy_from_settings
from presentation.telegram.runtime.fsm import FSMStore, State
from presentation.telegram.runtime.rate_limit import RateLimiter


def test_socks5_validation_and_auth() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_ENV="test", TELEGRAM_PROXY_ENABLED=True)
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="test",
            TELEGRAM_PROXY_ENABLED=True,
            TELEGRAM_PROXY_HOST="localhost",
            TELEGRAM_PROXY_PORT=1080,
            TELEGRAM_PROXY_USERNAME="user",
        )
    settings = Settings(
        APP_ENV="test",
        TELEGRAM_PROXY_ENABLED=True,
        TELEGRAM_PROXY_HOST="localhost",
        TELEGRAM_PROXY_PORT=1080,
        TELEGRAM_PROXY_USERNAME="user",
        TELEGRAM_PROXY_PASSWORD="pass",
    )
    assert proxy_from_settings(settings) == ("socks5", "localhost", 1080, True, "user", "pass")


def test_production_requires_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_ENV="production")


def test_env_example_is_a_valid_settings_template(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    template = Path(__file__).parents[2] / ".env.example"

    settings = Settings(_env_file=template)

    assert settings.APP_ENV == "development"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123456789", (123456789,)),
        ("123456789,987654321", (123456789, 987654321)),
        ("123456789,987654321,123456789", (123456789, 987654321)),
        (" 123456789 , 987654321 ", (123456789, 987654321)),
    ],
)
def test_multiple_superadmin_ids_are_parsed_and_deduplicated(
    raw: str, expected: tuple[int, ...]
) -> None:
    settings = Settings(APP_ENV="test", SUPERADMIN_TELEGRAM_IDS=raw)

    assert parse_superadmin_telegram_ids(raw) == expected
    assert settings.superadmin_telegram_ids() == expected


@pytest.mark.parametrize("raw", ["", "123,,456", "abc", "123,-456", "0", "9223372036854775808"])
def test_invalid_multiple_superadmin_ids_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError, match="SUPERADMIN_TELEGRAM_IDS"):
        Settings(APP_ENV="test", SUPERADMIN_TELEGRAM_IDS=raw)


def test_production_requires_postgresql_control_database() -> None:
    values = {
        "APP_ENV": "production",
        "MASTER_ENCRYPTION_KEY_B64": "test-key",
        "TELEGRAM_API_ID": 123,
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_BOT_TOKEN": "123:token",
        "SUPERADMIN_TELEGRAM_IDS": "1",
    }
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings(**values, DATABASE_URL="sqlite+aiosqlite:///production.sqlite3")
    settings = Settings(
        **values,
        DATABASE_URL="postgresql+asyncpg://app:password@postgres/app",
    )
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")


def test_delivery_retry_bounds_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="test",
            TELEGRAM_RETRY_BASE_SECONDS=61,
            TELEGRAM_RETRY_MAX_SECONDS=60,
        )


def test_source_resource_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="test",
            MAX_SOURCE_BYTES_PER_RUN=10,
            MAX_SOURCE_SINGLE_FILE_BYTES=11,
        )


def test_removed_decorative_settings_are_ignored_not_exposed() -> None:
    settings = Settings(
        APP_ENV="test",
        APP_NAME="legacy",
        APP_TIMEZONE="UTC",
        DISPLAY_TIMEZONE="UTC",
        TEMP_DIR="/legacy",
        TELEGRAM_UPLOAD_RETRIES=99,
    )
    for name in (
        "APP_NAME",
        "APP_TIMEZONE",
        "DISPLAY_TIMEZONE",
        "TEMP_DIR",
        "TELEGRAM_UPLOAD_RETRIES",
    ):
        assert not hasattr(settings, name)


@pytest.mark.asyncio
async def test_fsm_strict_transition_timeout_and_invalid_limit() -> None:
    store = FSMStore(30, 2)
    await store.set(1, State.ADD_ADMIN_NAME)
    with pytest.raises(ValueError):
        await store.transition(1, State.ADD_BACKUP_NAME, State.ADD_BACKUP_DESCRIPTION)
    assert await store.invalid(1) is False
    assert await store.invalid(1) is True
    assert await store.get(1) is None
    with patch("presentation.telegram.runtime.fsm.monotonic", side_effect=[0, 1000]):
        await store.set(2, State.ADD_ADMIN_NAME)
        assert await store.get(2) is None


@pytest.mark.asyncio
async def test_rate_limit_periodic_prune_removes_expired_user_buckets() -> None:
    limiter = RateLimiter(2, 10)
    with patch("presentation.telegram.runtime.rate_limit.monotonic", return_value=10):
        assert await limiter.allow(42)
    with patch("presentation.telegram.runtime.rate_limit.monotonic", return_value=71):
        assert await limiter.prune_expired() == 1
        assert await limiter.allow(42)


@pytest.mark.parametrize("worker_name", ["shared", "shared.session"])
def test_bot_worker_session_aliases_are_rejected(tmp_path: Path, worker_name: str) -> None:
    with pytest.raises(ValidationError, match="distinct Telegram session paths"):
        Settings(
            APP_ENV="test",
            TELEGRAM_SESSION_PATH=tmp_path / "shared.session",
            TELEGRAM_WORKER_SESSION_PATH=tmp_path / worker_name,
        )


def test_extensionless_session_paths_secure_the_telethon_filename(tmp_path: Path) -> None:
    settings = Settings(
        APP_ENV="test",
        TELEGRAM_SESSION_PATH=tmp_path / "bot",
        TELEGRAM_WORKER_SESSION_PATH=tmp_path / "worker",
    )
    assert tmp_path / "bot.session" == settings.TELEGRAM_SESSION_PATH
    assert tmp_path / "worker.session" == settings.TELEGRAM_WORKER_SESSION_PATH
