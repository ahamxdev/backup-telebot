import asyncio
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BackupPath, BackupProfile, FilesSource, User, utc_now
from domain.enums import (
    CompressionFormat,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStatus,
    SourceLocation,
    SourceType,
)
from infrastructure import ssh
from infrastructure.ssh import SSHConnectionTimeout, SSHCredentials, SSHGateway
from pipeline import transforms
from pipeline.acquisition import _validate_remote_rsync
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.jobs import RunService
from pipeline.process import ProcessResult
from pipeline.worker import PipelineRunner
from pipeline.workspace import create_workspace


async def test_overall_deadline_stops_stalled_work() -> None:
    deadline = RunDeadline(0.01)

    with pytest.raises(PipelineError) as caught:
        await deadline.wait_for(asyncio.sleep(60))

    assert caught.value.code == "BACKUP_TIMEOUT"


async def test_ssh_connect_has_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stalled_connect(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(ssh.asyncssh, "connect", stalled_connect)
    monkeypatch.setattr(SSHGateway, "_key", staticmethod(lambda _credentials: object()))
    credentials = SSHCredentials("host", 22, "user", "key", None)

    with pytest.raises(SSHConnectionTimeout):
        async with SSHGateway().pinned_connection(
            credentials, "SHA256:pinned", connect_timeout=0.01
        ):
            raise AssertionError("connection unexpectedly opened")


async def test_remote_rsync_check_obeys_remaining_run_deadline() -> None:
    class Connection:
        async def run(self, _command: str, **kwargs: object) -> None:
            await asyncio.sleep(cast(float, kwargs["timeout"]))
            raise TimeoutError

    with pytest.raises(PipelineError) as caught:
        await _validate_remote_rsync(cast(object, Connection()), RunDeadline(0.01))  # type: ignore[arg-type]

    assert caught.value.code == "BACKUP_TIMEOUT"


async def test_each_subprocess_receives_current_remaining_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target.gz"
    source.write_bytes(b"payload")
    observed: list[float] = []

    async def fake_run(
        _executable: Path, _args: tuple[str, ...], **kwargs: object
    ) -> ProcessResult:
        observed.append(cast(float, kwargs["timeout"]))
        target.write_bytes(b"archive")
        return ProcessResult(b"", "")

    remaining = iter((8.0, 3.0))
    monkeypatch.setattr(transforms, "run_process", fake_run)
    await transforms.compress(
        source,
        target,
        CompressionFormat.GZIP,
        transforms.CompressionPreset.BALANCED,
        threads=1,
        memory_mb=64,
        timeout=60,
        timeout_provider=lambda: next(remaining),
    )

    assert observed == [8.0, 3.0]


async def _claimed_local_run(session: AsyncSession, tmp_path: Path, owner_name: str):
    source = tmp_path / f"source-{owner_name}"
    source.mkdir()
    payload = source / "payload"
    payload.write_bytes(b"payload")
    owner = User(
        telegram_user_id=abs(hash(owner_name)) % 1_000_000 + 1,
        display_name=owner_name,
        role=Role.SUPERADMIN,
    )
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name=f"deadline-{owner_name}",
        source_type=SourceType.FILES_DIRECTORIES,
        enabled=True,
        status=ProfileStatus.DRAFT,
        compression_format=CompressionFormat.NONE,
        compression_preset=None,
        encryption_mode=EncryptionMode.NONE,
        created_by=owner.id,
        files_source=FilesSource(
            location=SourceLocation.LOCAL,
            paths=[BackupPath(path=str(payload), sqlite_snapshot_candidate=False)],
        ),
    )
    session.add(profile)
    await session.commit()
    queued = await RunService(session).enqueue_manual(owner, profile.id)
    claimed = await RunService(session).claim(owner_name, 120, (RunStatus.QUEUED,))
    assert claimed is not None
    return queued.run, source


def _runner_settings(settings, source: Path):
    return settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "ALLOWED_SOURCE_ROOTS": str(source),
            "MIN_FREE_DISK_BYTES": 0,
            "MAX_BACKUP_RUNTIME_SECONDS": 60,
        }
    )


async def test_timeout_marks_failed_and_cleans_only_owned_workspace(
    database, session: AsyncSession, settings, tmp_path: Path
) -> None:
    run, source = await _claimed_local_run(session, tmp_path, "timeout-owner")
    owned = create_workspace(settings.WORK_ROOT, run.uuid)
    other = create_workspace(settings.WORK_ROOT, "00000000-0000-4000-8000-000000000099")
    (owned.raw / "partial").write_bytes(b"partial")
    (other.raw / "keep").write_bytes(b"keep")
    run.workspace_path = str(owned.root)
    run.started_at = utc_now() - timedelta(seconds=61)
    await session.commit()

    await PipelineRunner(
        database.session_factory,
        _runner_settings(settings, source),
        object(),
        "timeout-owner",
    ).execute(run.id)

    await session.refresh(run)
    assert run.status is RunStatus.FAILED
    assert run.error_code == "BACKUP_TIMEOUT"
    assert run.lease_owner is None and run.lease_expires_at is None
    assert run.workspace_path is None and not owned.root.exists()
    assert (other.raw / "keep").read_bytes() == b"keep"


async def test_requested_cancellation_wins_timeout_race(
    database, session: AsyncSession, settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, source = await _claimed_local_run(session, tmp_path, "cancel-owner")

    async def timed_out_after_cancel(
        _runner: PipelineRunner,
        _session: AsyncSession,
        current,
        _deadline: RunDeadline,
    ) -> None:
        current.status = RunStatus.RUNNING
        current.cancel_requested = True
        await _session.commit()
        raise PipelineError("BACKUP_TIMEOUT", "backup run deadline exceeded")

    monkeypatch.setattr(PipelineRunner, "_build", timed_out_after_cancel)
    await PipelineRunner(
        database.session_factory,
        _runner_settings(settings, source),
        object(),
        "cancel-owner",
    ).execute(run.id)

    await session.refresh(run)
    assert run.status is RunStatus.CANCELLED
    assert run.error_code == "CANCELLED"


async def test_timeout_cannot_finalize_after_lease_moves_to_another_worker(
    database, session: AsyncSession, settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, source = await _claimed_local_run(session, tmp_path, "first-owner")

    async def timeout_after_lease_change(
        _runner: PipelineRunner,
        run_session: AsyncSession,
        current,
        _deadline: RunDeadline,
    ) -> None:
        current.status = RunStatus.RUNNING
        current.lease_owner = "replacement-owner"
        current.lease_expires_at = utc_now() + timedelta(seconds=120)
        await run_session.commit()
        raise PipelineError("BACKUP_TIMEOUT", "backup run deadline exceeded")

    monkeypatch.setattr(PipelineRunner, "_build", timeout_after_lease_change)
    await PipelineRunner(
        database.session_factory,
        _runner_settings(settings, source),
        object(),
        "first-owner",
    ).execute(run.id)

    await session.refresh(run)
    assert run.status is RunStatus.RUNNING
    assert run.lease_owner == "replacement-owner"
    assert run.error_code is None
