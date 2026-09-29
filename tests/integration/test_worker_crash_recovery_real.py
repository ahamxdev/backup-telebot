from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from database.models import BackupProfile, BackupRun, User
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
)
from pipeline.finalize import recover_finalized
from pipeline.jobs import RunService
from pipeline.worker import PipelineRunner
from pipeline.workspace import create_workspace

HELPER = Path(__file__).with_name("chaos_worker_process.py")


async def create_run(session, name: str) -> BackupRun:
    owner = User(
        telegram_user_id=800_000 + int(time.time_ns() % 100_000),
        display_name="chaos owner",
        role=Role.SUPERADMIN,
    )
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name=name,
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
    )
    session.add(profile)
    await session.flush()
    run = BackupRun(
        uuid=f"00000000-0000-4000-8000-{profile.id:012d}",
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
        stage=RunStage.QUEUED,
    )
    session.add(run)
    await session.commit()
    return run


async def kill_worker_at_stage(settings, run: BackupRun, stage: RunStage, tmp_path: Path) -> Path:
    ready = tmp_path / f"{stage.value}.ready"
    process = subprocess.Popen(  # noqa: S603, ASYNC220 - deliberate real process crash
        [
            sys.executable,
            str(HELPER),
            settings.DATABASE_URL,
            str(run.id),
            stage.value,
            str(settings.WORK_ROOT),
            str(settings.PENDING_DELIVERY_ROOT),
            str(ready),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists():
        if process.poll() is not None:
            detail = process.stderr.read() if process.stderr else "chaos worker exited"
            raise AssertionError(detail)
        await asyncio.sleep(0.02)
    assert ready.exists(), "chaos worker did not reach its durable stage"
    os.killpg(process.pid, signal.SIGKILL)
    await asyncio.to_thread(process.wait, 10)
    assert process.returncode == -signal.SIGKILL
    return {
        RunStage.COMPRESS: settings.WORK_ROOT / run.uuid / "staging" / "active-compressor.gz",
        RunStage.ENCRYPT: settings.WORK_ROOT / run.uuid / "final" / "active-encryption.age",
    }.get(stage, settings.WORK_ROOT / run.uuid / "raw" / "active-writer.partial")


async def concurrently_reclaim(database, run_id: int, status: RunStatus) -> str:
    async def claim(owner: str) -> tuple[str, int | None]:
        async with database.session_factory() as worker_session:
            item = await RunService(worker_session).claim(owner, 120, (status,))
            return owner, item.id if item is not None else None

    results = await asyncio.gather(claim("replacement-a"), claim("replacement-b"))
    winners = [(owner, claimed) for owner, claimed in results if claimed == run_id]
    assert len(winners) == 1
    return winners[0][0]


def configured_worker_settings(settings):
    return settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        }
    )


@pytest.mark.parametrize("stage", [RunStage.BACKUP, RunStage.COMPRESS, RunStage.ENCRYPT])
async def test_sigkill_during_pipeline_stage_is_fenced_and_recoverable(
    database, session, settings, tmp_path: Path, stage: RunStage
) -> None:
    run = await create_run(session, f"chaos-{stage.value.lower()}")
    unrelated_uuid = "00000000-0000-4000-8000-999999999999"
    unrelated = create_workspace(settings.WORK_ROOT, unrelated_uuid)
    source = tmp_path / "source-must-survive"
    source.write_text("source data")
    writer = await kill_worker_at_stage(settings, run, stage, tmp_path)
    size = writer.stat().st_size
    await asyncio.sleep(0.1)
    assert writer.stat().st_size == size

    await session.refresh(run)
    assert run.status is RunStatus.RUNNING
    assert await RunService(session).claim("too-early", 120, (RunStatus.RUNNING,)) is None
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    winner = await concurrently_reclaim(database, run.id, RunStatus.RUNNING)

    await session.refresh(run)
    run.cancel_requested = True
    await session.commit()
    await PipelineRunner(
        database.session_factory,
        configured_worker_settings(settings),
        object(),
        winner,
    ).execute(run.id)
    await session.refresh(run)
    assert run.status is RunStatus.CANCELLED
    assert run.lease_owner is None
    assert not settings.WORK_ROOT.joinpath(run.uuid).exists()
    assert unrelated.root.exists()
    assert source.read_text() == "source data"


async def test_sigkill_after_finalization_recovers_exact_pending_artifact_once(
    database, session, settings, tmp_path: Path
) -> None:
    run = await create_run(session, "chaos-finalized")
    writer = await kill_worker_at_stage(settings, run, RunStage.PENDING_DELIVERY, tmp_path)
    await session.refresh(run)
    assert run.status is RunStatus.DELIVERY_PENDING
    assert run.pending_directory is not None
    recovered = recover_finalized(
        settings.PENDING_DELIVERY_ROOT, Path(run.pending_directory), run.uuid
    )
    assert recovered is not None
    finalized, metadata = recovered
    assert finalized.checksum == run.final_sha256
    assert finalized.original_size_bytes == 8192
    assert metadata == {"engine": "chaos-fixture"}
    size = writer.stat().st_size
    await asyncio.sleep(0.1)
    assert writer.stat().st_size == size
    assert await RunService(session).claim("too-early", 120, (RunStatus.DELIVERY_PENDING,)) is None
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    winner = await concurrently_reclaim(database, run.id, RunStatus.DELIVERY_PENDING)
    await session.refresh(run)
    assert run.final_sha256 == finalized.checksum
    assert run.status is RunStatus.DELIVERY_PENDING

    run.cancel_requested = True
    await session.commit()
    await PipelineRunner(
        database.session_factory,
        configured_worker_settings(settings),
        object(),
        winner,
    ).execute(run.id)
    await session.refresh(run)
    assert run.status is RunStatus.CANCELLED
    assert run.lease_owner is None
    assert run.pending_directory is None
    assert not finalized.directory.exists()
