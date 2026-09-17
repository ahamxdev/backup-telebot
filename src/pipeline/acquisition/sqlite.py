import sqlite3
from pathlib import Path

from pipeline.acquisition.common import SourceBudget
from pipeline.deadline import RunDeadline
from pipeline.errors import PipelineError


def _snapshot_sqlite_fd(
    source_fd: int,
    target: Path,
    budget: SourceBudget | None = None,
    deadline: RunDeadline | None = None,
) -> None:
    descriptor_path = Path("/proc/self/fd") / str(source_fd)
    if not descriptor_path.exists():
        raise PipelineError("UNSAFE_SOURCE", "secure SQLite descriptor access is unavailable")
    source_uri = f"file:{descriptor_path}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as origin, sqlite3.connect(target) as copy:
        progress = None
        if budget is not None:

            def progress(_status: int, _remaining: int, _total: int) -> None:
                budget.check_disk()

        elif deadline is not None:

            def progress(_status: int, _remaining: int, _total: int) -> None:
                deadline.check()

        origin.backup(copy, pages=256, progress=progress)
        result = copy.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise PipelineError("SQLITE_VERIFY_FAILED", "SQLite integrity check failed")
