from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from database.models import BackupRun
from database.session import Database
from domain.enums import RunStage, RunStatus
from pipeline.finalize import finalize_artifact
from pipeline.workspace import create_workspace


def wait_for_nonempty(path: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if path.stat().st_size > 0:
                return
        except FileNotFoundError:
            pass
        time.sleep(0.001)
    raise RuntimeError("encryption process did not create its output")


async def stream_compression(writer: Path, ready: Path) -> None:
    with writer.open("xb") as output:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/gzip",
            "-c",
            stdin=asyncio.subprocess.PIPE,
            stdout=output,
        )
        assert process.stdin is not None
        ready.write_text(str(os.getpid()))  # noqa: ASYNC240 - killed externally
        while True:
            process.stdin.write(b"compressible-chaos-data" * 65536)
            await process.stdin.drain()


async def stream_encryption(writer: Path, ready: Path) -> None:
    keygen = await asyncio.create_subprocess_exec(
        "/usr/bin/age-keygen",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _private_key, keygen_stderr = await keygen.communicate()
    assert keygen.returncode == 0
    match = re.search(rb"Public key: (age1[a-z0-9]+)", keygen_stderr)
    assert match is not None
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/age",
        "--recipient",
        match.group(1).decode(),
        "--output",
        str(writer),
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None
    process.stdin.write(os.urandom(1024 * 1024))
    await process.stdin.drain()
    await asyncio.to_thread(wait_for_nonempty, writer)
    ready.write_text(str(os.getpid()))  # noqa: ASYNC240 - killed externally
    while True:
        process.stdin.write(os.urandom(1024 * 1024))
        await process.stdin.drain()


async def main() -> None:
    database_url, run_id_value, stage_value, work_value, pending_value, ready_value = sys.argv[1:]
    run_id = int(run_id_value)
    work_root = Path(work_value)
    pending_root = Path(pending_value)
    ready = Path(ready_value)
    database = Database(database_url)
    try:
        async with database.session_factory() as session:
            run = await session.get(BackupRun, run_id)
            assert run is not None
            run.status = RunStatus.RUNNING
            run.stage = RunStage(stage_value)
            run.lease_owner = "killed-worker"
            run.lease_expires_at = datetime.now(UTC) + timedelta(hours=1)
            run.started_at = datetime.now(UTC)
            workspace = create_workspace(work_root, run.uuid)
            run.workspace_path = str(workspace.root)
            writer = workspace.raw / "active-writer.partial"
            if stage_value == RunStage.PENDING_DELIVERY.value:
                source = workspace.final / "artifact.tar"
                source.write_bytes(b"fully-finalized-chaos-artifact")
                finalized = finalize_artifact(
                    source,
                    pending_root,
                    "chaos profile",
                    run.uuid,
                    datetime.now(UTC),
                    "UTC",
                    "artifact.tar",
                    {"engine": "chaos-fixture"},
                    original_size_bytes=8192,
                )
                run.status = RunStatus.DELIVERY_PENDING
                run.stage = RunStage.PENDING_DELIVERY
                run.pending_directory = str(finalized.directory)
                run.final_filename = finalized.artifact.name
                run.final_size_bytes = finalized.size
                run.final_sha256 = finalized.checksum
            await session.commit()
        if stage_value == RunStage.COMPRESS.value:
            writer = workspace.staging / "active-compressor.gz"
            await stream_compression(writer, ready)
        elif stage_value == RunStage.ENCRYPT.value:
            writer = workspace.final / "active-encryption.age"
            await stream_encryption(writer, ready)
        else:
            ready.write_text(str(os.getpid()))  # noqa: ASYNC240 - killed externally
            with writer.open("ab", buffering=0) as stream:
                while True:
                    stream.write(b"x" * 4096)
                    os.fsync(stream.fileno())
                    await asyncio.sleep(0.01)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
