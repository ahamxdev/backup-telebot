import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from pipeline.errors import PipelineError
from pipeline.process import (
    _terminate_process_group,
    run_age_passphrase,
    run_process,
)


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 424_242
        self.returncode: int | None = None
        self.wait_calls = 0
        self._exited = asyncio.Event()

    def exit(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self._exited.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
        await self._exited.wait()
        return b"", b""


async def test_process_exit_before_term_is_still_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProcess()
    child.exit()
    signals: list[signal.Signals] = []
    monkeypatch.setattr(os, "killpg", lambda _pid, sig: signals.append(sig))

    await _terminate_process_group(child)  # type: ignore[arg-type]

    assert signals == []
    assert child.wait_calls == 1


async def test_process_exit_racing_term_is_harmless_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProcess()
    signals: list[signal.Signals] = []

    def raced_killpg(_pid: int, sig: signal.Signals) -> None:
        signals.append(sig)
        child.exit()
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", raced_killpg)

    await _terminate_process_group(child)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM]
    assert child.wait_calls == 1


async def test_process_exit_between_term_and_kill_is_harmless_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProcess()
    signals: list[signal.Signals] = []

    def raced_killpg(_pid: int, sig: signal.Signals) -> None:
        signals.append(sig)
        if sig is signal.SIGKILL:
            child.exit()
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", raced_killpg)

    await _terminate_process_group(child, grace_seconds=0)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert child.wait_calls == 1


async def test_repeated_process_cleanup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _FakeProcess()
    signals: list[signal.Signals] = []

    def killpg(_pid: int, sig: signal.Signals) -> None:
        signals.append(sig)
        child.exit(-sig)

    monkeypatch.setattr(os, "killpg", killpg)

    await _terminate_process_group(child)  # type: ignore[arg-type]
    await _terminate_process_group(child)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM]
    assert child.wait_calls == 2


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_run_process_preserves_timeout_or_cancellation_during_exit_race(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    child = _FakeProcess()

    async def create_subprocess(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return child

    def killpg(_pid: int, _sig: signal.Signals) -> None:
        child.exit(-signal.SIGTERM)
        raise ProcessLookupError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(os, "killpg", killpg)
    task = asyncio.create_task(run_process(Path("/bin/tool"), (), timeout=0.01))
    if failure == "cancel":
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(PipelineError) as caught:
            await task
        assert caught.value.code == "PROCESS_TIMEOUT"
    assert child.wait_calls == 1
    assert child.returncode is not None


async def test_pty_subprocess_is_terminated_and_reaped_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[asyncio.subprocess.Process] = []
    real_create = asyncio.create_subprocess_exec

    async def capturing_create(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await real_create(*args, **kwargs)
        created.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capturing_create)
    task = asyncio.create_task(
        run_age_passphrase(
            Path(sys.executable),
            ("-c", "import time; time.sleep(60)"),
            "not-a-real-secret",
            timeout=60,
        )
    )
    for _ in range(100):
        if created:
            break
        await asyncio.sleep(0.01)
    assert created

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    child = created[0]
    assert child.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)


async def test_shutdown_cancellation_reaps_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[asyncio.subprocess.Process] = []
    real_create = asyncio.create_subprocess_exec

    async def capturing_create(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await real_create(*args, **kwargs)
        created.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capturing_create)
    active = {
        asyncio.create_task(
            run_process(
                Path(sys.executable),
                ("-c", "import time; time.sleep(60)"),
                timeout=60,
            )
        )
    }
    for _ in range(100):
        if created:
            break
        await asyncio.sleep(0.01)
    assert created

    for task in active:
        task.cancel()
    results = await asyncio.gather(*active, return_exceptions=True)

    assert isinstance(results[0], asyncio.CancelledError)
    child = created[0]
    assert child.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)
