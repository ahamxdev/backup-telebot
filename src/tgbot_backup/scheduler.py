"""Lightweight cron/interval scheduler for backup jobs.

Two schedule formats are supported:
  Cron expression : "0 3 * * *"  (minute hour dom month dow)
  Interval        : "every 6h"  |  "every 30m"  |  "every 1d"

Cron DOW convention: 0=Sunday … 6=Saturday (7 also accepted as Sunday).
Intervals use wall-clock UTC offsets; they are NOT reset by DST changes.

Each job is tracked by name. JobScheduler is NOT thread-safe itself —
callers must use try_acquire/release around job execution.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interval parsing
# ---------------------------------------------------------------------------

_INTERVAL_RE = re.compile(
    r"^every\s+(\d+(?:\.\d+)?)\s*"
    r"(s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hr?s?|hour?s?|d|days?)$",
    re.IGNORECASE,
)
_UNIT_SECONDS: dict[str, float] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


def parse_interval_seconds(expr: str) -> float | None:
    """Return interval in seconds for 'every X unit' expressions, or None if not matched."""
    m = _INTERVAL_RE.match(expr.strip())
    if not m:
        return None
    amount, raw_unit = float(m.group(1)), m.group(2).lower()
    for suffix, secs in _UNIT_SECONDS.items():
        if raw_unit == suffix or raw_unit.rstrip("s") == suffix.rstrip("s"):
            return amount * secs
    # fallback: exact match
    return amount * _UNIT_SECONDS.get(raw_unit, 1)


# ---------------------------------------------------------------------------
# Cron field parser
# ---------------------------------------------------------------------------


def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    """Return the set of integers matched by one cron field within [lo, hi]."""
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.rsplit("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"Bad step in cron field {field!r}")

        if part == "*":
            rng: Iterator[int] = iter(range(lo, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            rng = iter(range(int(a), int(b) + 1, step))
        else:
            v = int(part)
            rng = iter(range(v, hi + 1, step)) if step != 1 else iter([v])

        for v in rng:
            if lo <= v <= hi:
                values.add(v)
    return values


# ---------------------------------------------------------------------------
# Schedule types
# ---------------------------------------------------------------------------


class CronSchedule:
    """5-field cron schedule (minute hour dom month dow)."""

    def __init__(self, expr: str) -> None:
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression needs 5 fields, got: {expr!r}")
        self._minutes = _parse_cron_field(parts[0], 0, 59)
        self._hours = _parse_cron_field(parts[1], 0, 23)
        self._doms = _parse_cron_field(parts[2], 1, 31)
        self._months = _parse_cron_field(parts[3], 1, 12)
        # DOW: accept 0-7 (map 7→0 for Sunday)
        raw_dows = _parse_cron_field(parts[4], 0, 7)
        self._dows: set[int] = {0 if d == 7 else d for d in raw_dows}
        self._expr = expr

    def next_after(self, dt: datetime) -> datetime:
        """Return the next UTC datetime strictly after *dt* matching this schedule."""
        # Work in minute-granularity; start at next minute
        cur = dt.replace(second=0, microsecond=0, tzinfo=timezone.utc) + timedelta(minutes=1)
        limit = cur + timedelta(days=4 * 366)  # guard against unsatisfiable schedules

        while cur <= limit:
            if cur.month not in self._months:
                cur += timedelta(minutes=1)
                continue
            if cur.day not in self._doms:
                cur += timedelta(minutes=1)
                continue
            # Python weekday: Mon=0 … Sun=6  →  cron DOW: Sun=0 Mon=1 … Sat=6
            cron_dow = (cur.weekday() + 1) % 7
            if cron_dow not in self._dows:
                cur += timedelta(minutes=1)
                continue
            if cur.hour not in self._hours:
                cur += timedelta(minutes=1)
                continue
            if cur.minute not in self._minutes:
                cur += timedelta(minutes=1)
                continue
            return cur

        raise RuntimeError(f"No next run found for cron {self._expr!r} within 4 years")

    def __repr__(self) -> str:
        return f"CronSchedule({self._expr!r})"


class IntervalSchedule:
    """Fixed-interval schedule (e.g. 'every 6h')."""

    def __init__(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("Interval must be positive")
        self._seconds = seconds

    def next_after(self, dt: datetime) -> datetime:
        return dt + timedelta(seconds=self._seconds)

    def __repr__(self) -> str:
        return f"IntervalSchedule({self._seconds}s)"


def make_schedule(expr: str) -> CronSchedule | IntervalSchedule:
    """Parse a schedule expression string into a schedule object."""
    secs = parse_interval_seconds(expr)
    if secs is not None:
        return IntervalSchedule(secs)
    try:
        return CronSchedule(expr)
    except ValueError as exc:
        raise ValueError(
            f"Unrecognized schedule {expr!r}.\n"
            "  Cron examples:     '0 3 * * *'  '*/15 * * * *'\n"
            "  Interval examples: 'every 6h'   'every 30m'   'every 1d'"
        ) from exc


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class JobScheduler:
    """Tracks next-run times and prevents overlapping runs via per-job locks.

    Not thread-safe: the caller must hold an external lock if multiple threads
    call due_jobs / try_acquire concurrently.
    """

    def __init__(self) -> None:
        self._schedules: dict[str, CronSchedule | IntervalSchedule] = {}
        self._next_run: dict[str, datetime] = {}
        self._locks: dict[str, threading.Lock] = {}

    def add_job(self, name: str, schedule_expr: str, *, now: datetime | None = None) -> None:
        sched = make_schedule(schedule_expr)
        self._schedules[name] = sched
        self._locks[name] = threading.Lock()

        base = (now or datetime.now(tz=timezone.utc)).replace(tzinfo=timezone.utc)
        self._next_run[name] = sched.next_after(base)
        logger.info(
            "Scheduled job %r (%r) — first run %s",
            name,
            schedule_expr,
            self._next_run[name].strftime("%Y-%m-%d %H:%M UTC"),
        )

    def due_jobs(self, now: datetime | None = None) -> list[str]:
        """Return names of jobs whose next_run <= now."""
        ts = (now or datetime.now(tz=timezone.utc)).replace(tzinfo=timezone.utc)
        return [n for n, t in self._next_run.items() if t <= ts]

    def try_acquire(self, name: str) -> bool:
        """Non-blocking attempt to mark job as running. Returns False if already running."""
        return self._locks[name].acquire(blocking=False)

    def release(self, name: str) -> None:
        self._locks[name].release()

    def mark_ran(self, name: str, ran_at: datetime | None = None) -> None:
        """Advance next_run after a job completes."""
        base = (ran_at or datetime.now(tz=timezone.utc)).replace(tzinfo=timezone.utc)
        self._next_run[name] = self._schedules[name].next_after(base)
        logger.info(
            "Job %r next run: %s",
            name,
            self._next_run[name].strftime("%Y-%m-%d %H:%M UTC"),
        )

    def seconds_until_next(self, now: datetime | None = None) -> float:
        """Seconds until the soonest upcoming job, or infinity if no jobs."""
        if not self._next_run:
            return float("inf")
        ts = (now or datetime.now(tz=timezone.utc)).replace(tzinfo=timezone.utc)
        return max(0.0, (min(self._next_run.values()) - ts).total_seconds())
