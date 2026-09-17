import asyncio
import inspect
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TypeVar

from pipeline.errors import PipelineError

T = TypeVar("T")


class RunDeadline:
    """One monotonic deadline, optionally reduced by persisted run elapsed time."""

    def __init__(self, seconds: float, *, started_at: datetime | None = None) -> None:
        elapsed = 0.0
        if started_at is not None:
            started = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at
            elapsed = max(0.0, (datetime.now(UTC) - started.astimezone(UTC)).total_seconds())
        self._expires_at = time.monotonic() + max(0.0, seconds - elapsed)

    def remaining(self) -> float:
        remaining = self._expires_at - time.monotonic()
        if remaining <= 0:
            raise PipelineError("BACKUP_TIMEOUT", "backup run deadline exceeded")
        return remaining

    def check(self) -> None:
        self.remaining()

    def operation_timeout(self, maximum: float | None = None) -> float:
        remaining = self.remaining()
        return remaining if maximum is None else min(remaining, maximum)

    async def wait_for(
        self,
        awaitable: Awaitable[T],
        *,
        maximum: float | None = None,
        timeout_code: str = "BACKUP_TIMEOUT",
        timeout_message: str = "backup run deadline exceeded",
    ) -> T:
        try:
            remaining = self.remaining()
        except PipelineError:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        timeout = remaining if maximum is None else min(remaining, maximum)
        operation_limited = maximum is not None and maximum < remaining
        try:
            async with asyncio.timeout(timeout):
                return await awaitable
        except TimeoutError as exc:
            code = timeout_code if operation_limited else "BACKUP_TIMEOUT"
            message = timeout_message if operation_limited else "backup run deadline exceeded"
            raise PipelineError(code, message) from exc
