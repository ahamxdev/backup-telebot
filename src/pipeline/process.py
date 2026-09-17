import asyncio
import os
import signal
import termios
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pipeline.errors import PipelineError

_STDERR_LIMIT = 16_384
_STDOUT_LIMIT = 16_384
_TERMINATION_GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: str


class ProcessExecutionError(PipelineError):
    """Bounded subprocess diagnostics for callers which can safely classify them."""

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__("PROCESS_FAILED", stderr or "backup tool failed")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _signal_process_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Signal an owned process group unless it has already been reaped."""
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


async def _terminate_process_group(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
    wait_task: asyncio.Task[int] | None = None,
) -> None:
    """Race-safely terminate and reap an owned subprocess process group.

    ``ProcessLookupError`` is an expected outcome when the child exits between
    the return-code check and ``killpg``.  A shielded wait task is retained
    across the TERM grace timeout so cancelling that timeout cannot cancel the
    actual reap operation.
    """
    reap_task = wait_task if wait_task is not None else asyncio.create_task(process.wait())
    try:
        _signal_process_group(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(reap_task), timeout=grace_seconds)
        except TimeoutError:
            _signal_process_group(process, signal.SIGKILL)
    except asyncio.CancelledError:
        _signal_process_group(process, signal.SIGKILL)
        raise
    finally:
        await asyncio.shield(reap_task)


async def run_age_passphrase(
    executable: Path,
    args: tuple[str, ...],
    passphrase: str,
    *,
    timeout: float,
    capacity_check: Callable[[], None] | None = None,
) -> None:
    """Send an Age passphrase over a private PTY, never argv or environment."""
    master, slave = os.openpty()
    attributes = termios.tcgetattr(slave)
    attributes[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/setsid",
            "--ctty",
            "--wait",
            str(executable),
            *args,
            stdin=slave,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=slave,
        )
        os.close(slave)
        slave = -1
        os.write(master, passphrase.encode() + b"\n" + passphrase.encode() + b"\n")
        wait_task = asyncio.create_task(process.wait())
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        return_code = await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
                        break
                    except TimeoutError:
                        if capacity_check is not None:
                            capacity_check()
        except (TimeoutError, asyncio.CancelledError, PipelineError) as exc:
            await _terminate_process_group(process, wait_task=wait_task)
            if isinstance(exc, PipelineError):
                raise
            raise
        if return_code != 0:
            raise PipelineError("PROCESS_FAILED", "age passphrase encryption failed")
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)


async def run_process(
    executable: Path,
    args: tuple[str, ...],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdout_path: Path | None = None,
    capacity_check: Callable[[], None] | None = None,
    stdin_data: bytes | None = None,
) -> ProcessResult:
    """Run an absolute, server-selected executable without a shell."""
    if not executable.is_absolute():
        raise ValueError("executable must be an absolute path")
    output: int | BinaryIO = asyncio.subprocess.PIPE
    stream: BinaryIO | None = None
    if stdout_path is not None:
        stream = stdout_path.open("wb")
        output = stream
    try:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=output,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except Exception:
        if stream is not None:
            stream.close()
        raise
    try:
        async with asyncio.timeout(timeout):
            communication = asyncio.create_task(process.communicate(stdin_data))
            try:
                while True:
                    done, _pending = await asyncio.wait((communication,), timeout=0.5)
                    if communication in done:
                        stdout, stderr = communication.result()
                        break
                    if capacity_check is not None:
                        capacity_check()
            finally:
                if not communication.done():
                    communication.cancel()
                    with suppress(asyncio.CancelledError):
                        await communication
    except BaseException as exc:
        await _terminate_process_group(process)
        if not isinstance(exc, TimeoutError):
            raise
        raise PipelineError("PROCESS_TIMEOUT", "backup tool timed out") from exc
    finally:
        if stream is not None:
            stream.close()
    safe_stderr = (stderr or b"")[-_STDERR_LIMIT:].decode(errors="replace")
    if process.returncode != 0:
        safe_stdout = (stdout or b"")[-_STDOUT_LIMIT:].decode(errors="replace")
        raise ProcessExecutionError(process.returncode or -1, safe_stdout, safe_stderr)
    return ProcessResult(stdout or b"", safe_stderr)
