import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BackupProfile, BackupRun, DatabaseSource, User
from domain.enums import (
    CompressionFormat,
    DatabaseEngine,
    EncryptionMode,
    ProfileStatus,
    Role,
    RunStage,
    RunStatus,
    RunTrigger,
    SourceType,
    TLSMode,
)
from pipeline import acquisition
from pipeline.acquisition import _mssql, _mssql_execution_path
from pipeline.cooperative import CooperativeStop, run_cooperative_thread
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError
from pipeline.process import ProcessResult, run_process
from pipeline.worker import PipelineRunner
from security import SecretCipher


def _mssql_source() -> DatabaseSource:
    return DatabaseSource(
        profile_id=1,
        engine=DatabaseEngine.MSSQL,
        host="sql.internal",
        port=1433,
        database_name="production",
        username="backup-user",
        encrypted_password=b"unused",
        tls_mode=TLSMode.DISABLED,
        mssql_in_docker=False,
        mssql_backup_path="/var/opt/mssql/backup/production.bak",
    )


def _cipher() -> SecretCipher:
    return SecretCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


def test_mssql_reclaimed_attempts_use_distinct_server_artifacts() -> None:
    path = "/var/opt/mssql/backup/production.bak"
    first = _mssql_execution_path(path, "1" * 32)
    second = _mssql_execution_path(path, "2" * 32)

    assert first.endswith(f".{'1' * 32}.bak")
    assert second.endswith(f".{'2' * 32}.bak")
    assert first != second


async def test_cancellation_during_mssql_backup_stops_child_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    observed: dict[str, object] = {}

    async def stalled_process(executable: Path, args: tuple[str, ...], **kwargs: object):
        observed.update(executable=executable, args=args, stdin=kwargs["stdin_data"])
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(acquisition, "run_process", stalled_process)
    task = asyncio.create_task(
        _mssql(
            _mssql_source(),
            tmp_path / "result.bak",
            "never-in-argv",
            _cipher(),
            RunDeadline(60),
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert observed["executable"] == Path(sys.executable)
    assert "never-in-argv" not in " ".join(observed["args"])  # type: ignore[arg-type]
    assert json.loads(observed["stdin"])["password"] == "never-in-argv"  # type: ignore[arg-type]


async def test_timeout_during_mssql_backup_is_reported_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def timed_out_process(*_args: object, **_kwargs: object) -> ProcessResult:
        raise PipelineError("PROCESS_TIMEOUT", "backup tool timed out")

    monkeypatch.setattr(acquisition, "run_process", timed_out_process)
    with pytest.raises(PipelineError) as caught:
        await _mssql(
            _mssql_source(),
            tmp_path / "result.bak",
            "secret-password",
            _cipher(),
            RunDeadline(60),
        )

    assert caught.value.code == "MSSQL_TIMEOUT"
    assert "secret-password" not in str(caught.value)


async def test_mssql_result_is_fenced_before_and_after_artifact_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_artifact = tmp_path / "server.bak"
    server_artifact.write_bytes(b"verified-backup")
    source = _mssql_source()
    source.mssql_backup_path = str(server_artifact)
    checks: list[int] = []

    async def successful_process(*_args: object, **_kwargs: object) -> ProcessResult:
        return ProcessResult(
            json.dumps(
                {
                    "ok": True,
                    "metadata": {
                        "engine": "mssql",
                        "native_compression": True,
                    },
                }
            ).encode(),
            "",
        )

    async def ownership_check() -> None:
        checks.append(len(checks) + 1)

    monkeypatch.setattr(acquisition, "run_process", successful_process)
    target = tmp_path / "result.bak"
    await _mssql(
        source,
        target,
        "secret-password",
        _cipher(),
        RunDeadline(60),
        ownership_check,
    )

    assert checks == [1, 2, 3]
    assert target.read_bytes() == b"verified-backup"


async def test_cancelled_process_has_no_background_writer(tmp_path: Path) -> None:
    output = tmp_path / "writer-output"
    script = (
        "import pathlib,sys,time;"
        "p=pathlib.Path(sys.argv[1]);"
        "f=p.open('ab',buffering=0);"
        "exec(\"while True:\\n f.write(b'x')\\n time.sleep(0.01)\")"
    )
    task = asyncio.create_task(
        run_process(
            Path(sys.executable),
            ("-c", script, str(output)),
            timeout=60,
        )
    )
    for _ in range(100):
        if output.exists() and output.stat().st_size:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    size_after_cancel = output.stat().st_size
    await asyncio.sleep(0.1)
    assert output.stat().st_size == size_after_cancel


async def test_cleanup_waits_until_cooperative_writer_exits(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "partial"
    stop = CooperativeStop()
    exited = asyncio.Event()

    def writer() -> None:
        with output.open("wb") as stream:
            while True:
                stop.check()
                stream.write(b"x" * 4096)
                time.sleep(0.001)

    async def supervised_writer() -> None:
        try:
            await run_cooperative_thread(writer, stop)
        finally:
            exited.set()

    task = asyncio.create_task(supervised_writer())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()
    shutil.rmtree(workspace)
    assert not workspace.exists()


async def test_stale_writer_cannot_corrupt_reclaimed_output(tmp_path: Path) -> None:
    output = tmp_path / "run-output"
    stop = CooperativeStop()

    def stale_writer() -> None:
        with output.open("wb") as stream:
            while True:
                stop.check()
                stream.write(b"stale\n")
                time.sleep(0.001)

    task = asyncio.create_task(run_cooperative_thread(stale_writer, stop))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    output.write_bytes(b"replacement-worker")
    await asyncio.sleep(0.05)
    assert output.read_bytes() == b"replacement-worker"


async def test_lease_loss_fence_rejects_stale_execution(
    database, session: AsyncSession, settings
) -> None:
    owner = User(telegram_user_id=8801, display_name="root", role=Role.SUPERADMIN)
    session.add(owner)
    await session.flush()
    profile = BackupProfile(
        name="lease-fence",
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
        uuid="00000000-0000-4000-8000-000000008801",
        profile_id=profile.id,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        stage=RunStage.FINALIZE,
        lease_owner="stale-worker",
    )
    session.add(run)
    await session.commit()
    configured = settings.model_copy(
        update={
            "MASTER_ENCRYPTION_KEY_B64": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        }
    )
    runner = PipelineRunner(database.session_factory, configured, object(), "stale-worker")

    async with database.session_factory() as replacement_session:
        replacement = await replacement_session.get(BackupRun, run.id)
        assert replacement is not None
        replacement.lease_owner = "replacement-worker"
        await replacement_session.commit()

    with pytest.raises(RuntimeError, match="lease ownership lost"):
        await runner._ownership_boundary(session, run)
