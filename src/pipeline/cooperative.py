import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress

from pipeline.errors import PipelineError


class CooperativeStop:
    """A thread-visible stop signal checked by long-running local operations."""

    def __init__(self, progress_check: Callable[[], None] | None = None) -> None:
        self._stopped = threading.Event()
        self._progress_check = progress_check

    def request(self) -> None:
        self._stopped.set()

    def check(self) -> None:
        if self._stopped.is_set():
            raise PipelineError("OPERATION_STOPPED", "backup operation stopped")
        if self._progress_check is not None:
            self._progress_check()


async def run_cooperative_thread[T](function: Callable[[], T], stop: CooperativeStop) -> T:
    """Do not return cancellation to the caller until the writer has actually stopped."""
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.request()
        with suppress(Exception, asyncio.CancelledError):
            await task
        raise
