from unittest.mock import AsyncMock

import pytest

from application import health_service
from application.health_service import ToolHealth, migration_head, validate_configured_tools
from bootstrap.app import run_bot
from pipeline.worker import run_worker


@pytest.mark.asyncio
async def test_bot_and_worker_fail_before_startup_without_master_key(settings) -> None:
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY_B64"):
        await run_bot(settings)
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY_B64"):
        await run_worker(settings)


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
    assert migration_head() == "0016"
