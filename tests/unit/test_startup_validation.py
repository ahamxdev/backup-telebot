import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from application import health_service
from application.health_service import ToolHealth, migration_head, validate_configured_tools
from bootstrap import app as bot_app
from bootstrap import worker as worker_app
from bootstrap.app import run_bot
from config.settings import Settings
from pipeline.worker import run_worker


@pytest.mark.asyncio
async def test_bot_and_worker_fail_before_startup_without_master_key(settings) -> None:
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY_B64"):
        await run_bot(settings)
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY_B64"):
        await run_worker(settings)


def production_settings(settings: Settings) -> Settings:
    values = settings.model_dump()
    values.update(
        APP_ENV="production",
        DATABASE_URL="postgresql+asyncpg://app:password@postgres/app",
        MASTER_ENCRYPTION_KEY_B64=base64.b64encode(b"k" * 32).decode(),
    )
    return Settings.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "runner"),
    ((bot_app, bot_app.run_bot), (worker_app, worker_app.run_worker)),
)
async def test_production_database_failure_prevents_runtime_startup_and_disposes(
    settings, monkeypatch: pytest.MonkeyPatch, module, runner
) -> None:
    events: list[str] = []
    database = MagicMock()

    async def fail_probe() -> None:
        events.append("probe")
        raise RuntimeError("PostgreSQL startup readiness check failed")

    async def dispose() -> None:
        events.append("dispose")

    database.verify_postgresql_ready = fail_probe
    database.dispose = dispose
    database.session_factory = MagicMock()
    client_builder = MagicMock()
    monkeypatch.setattr(module, "Database", MagicMock(return_value=database))
    monkeypatch.setattr(module, "build_client", client_builder)

    with pytest.raises(RuntimeError, match="PostgreSQL startup readiness check failed"):
        await runner(production_settings(settings))

    assert events == ["probe", "dispose"]
    database.session_factory.assert_not_called()
    client_builder.assert_not_called()


@pytest.mark.asyncio
async def test_reachable_production_database_precedes_telegram_startup(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    session = MagicMock()
    session.commit = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    database = MagicMock()
    database.session_factory = MagicMock(return_value=session_context)
    database.verify_postgresql_ready = AsyncMock(side_effect=lambda: events.append("probe"))
    database.dispose = AsyncMock(side_effect=lambda: events.append("dispose"))

    client = MagicMock()
    client.start = AsyncMock(side_effect=lambda **_kwargs: events.append("telegram_start"))
    client.run_until_disconnected = AsyncMock()
    client.disconnect = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.run_state_cleanup = AsyncMock()
    dispatcher.clear_transient_state = AsyncMock()

    monkeypatch.setattr(bot_app, "Database", MagicMock(return_value=database))
    monkeypatch.setattr(bot_app, "build_client", MagicMock(return_value=client))
    monkeypatch.setattr(bot_app, "TelegramDispatcher", MagicMock(return_value=dispatcher))
    monkeypatch.setattr(bot_app, "validate_runtime_schema", AsyncMock())
    monkeypatch.setattr(bot_app, "snapshot_legacy_active_runs", AsyncMock())
    monkeypatch.setattr(bot_app.BootstrapService, "bootstrap_superadmin", AsyncMock())
    monkeypatch.setattr(bot_app.AuditRepository, "record", AsyncMock())

    await bot_app.run_bot(production_settings(settings))

    assert events == ["probe", "telegram_start", "dispose"]


@pytest.mark.asyncio
async def test_startup_validation_names_only_missing_configured_tools(
    session, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = AsyncMock(
        return_value=(
            ToolHealth("safe-tool", True, "1.0"),
            ToolHealth("missing-tool", False, "missing"),
        )
    )
    monkeypatch.setattr(health_service, "configured_tool_health", probe)

    with pytest.raises(RuntimeError, match="required backup tools unavailable: missing-tool"):
        await validate_configured_tools(session, settings)


def test_migration_head_is_resolved_from_flat_source_layout() -> None:
    assert migration_head() == "0019"
