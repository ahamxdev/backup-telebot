"""Tests for the cron/interval scheduler."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from tgbot_backup.scheduler import (
    CronSchedule,
    IntervalSchedule,
    JobScheduler,
    make_schedule,
    parse_interval_seconds,
)


def dt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_interval_seconds
# ---------------------------------------------------------------------------


def test_parse_interval_seconds_hours():
    assert parse_interval_seconds("every 2h") == 7200


def test_parse_interval_seconds_minutes():
    assert parse_interval_seconds("every 30m") == 1800


def test_parse_interval_seconds_days():
    assert parse_interval_seconds("every 1d") == 86400


def test_parse_interval_seconds_seconds():
    assert parse_interval_seconds("every 60s") == 60


def test_parse_interval_seconds_invalid():
    assert parse_interval_seconds("0 3 * * *") is None


def test_parse_interval_seconds_invalid_unit():
    assert parse_interval_seconds("every 5x") is None


# ---------------------------------------------------------------------------
# IntervalSchedule
# ---------------------------------------------------------------------------


def test_interval_next_after():
    sched = IntervalSchedule(3600)
    t0 = dt(2024, 1, 1, 0, 0)
    t1 = sched.next_after(t0)
    assert t1 == dt(2024, 1, 1, 1, 0)


def test_interval_stacks():
    sched = IntervalSchedule(600)
    t0 = dt(2024, 1, 1, 0, 0)
    t1 = sched.next_after(t0)
    t2 = sched.next_after(t1)
    assert (t2 - t0).total_seconds() == 1200


# ---------------------------------------------------------------------------
# CronSchedule
# ---------------------------------------------------------------------------


def test_cron_every_hour():
    sched = CronSchedule("0 * * * *")
    t = sched.next_after(dt(2024, 1, 1, 3, 15))
    assert t == dt(2024, 1, 1, 4, 0)


def test_cron_daily():
    sched = CronSchedule("0 3 * * *")
    t = sched.next_after(dt(2024, 1, 1, 3, 0))
    # exactly at 03:00 — next is tomorrow 03:00
    assert t == dt(2024, 1, 2, 3, 0)


def test_cron_daily_before():
    sched = CronSchedule("0 3 * * *")
    t = sched.next_after(dt(2024, 1, 1, 2, 59))
    assert t == dt(2024, 1, 1, 3, 0)


def test_cron_every_15_minutes():
    sched = CronSchedule("*/15 * * * *")
    t = sched.next_after(dt(2024, 1, 1, 1, 1))
    assert t == dt(2024, 1, 1, 1, 15)


def test_cron_every_15_minutes_at_boundary():
    sched = CronSchedule("*/15 * * * *")
    t = sched.next_after(dt(2024, 1, 1, 1, 15))
    # at the boundary, next_after skips at least 1 minute
    assert t == dt(2024, 1, 1, 1, 30)


def test_cron_weekday_mapping_sunday():
    # Cron DOW 0 = Sunday; Python weekday 6 = Sunday
    # 2024-01-07 is a Sunday
    sched = CronSchedule("0 12 * * 0")  # Sunday noon
    t = sched.next_after(dt(2024, 1, 1, 0, 0))  # Monday
    assert t.weekday() == 6  # Python Sunday


def test_cron_weekday_7_is_sunday():
    # cron allows DOW 7 to mean Sunday as well
    sched = CronSchedule("0 12 * * 7")
    t = sched.next_after(dt(2024, 1, 1, 0, 0))
    assert t.weekday() == 6


def test_cron_invalid_field_count():
    with pytest.raises(ValueError, match="5 fields"):
        CronSchedule("0 3 * *")


def test_cron_out_of_range_value_ignored():
    # Out-of-range values (minute 60) are silently dropped from the match set.
    # The schedule effectively has no valid minutes, so next_after will raise RuntimeError.
    sched = CronSchedule("60 * * * *")
    with pytest.raises(RuntimeError, match="No next run"):
        sched.next_after(dt(2024, 1, 1, 0, 0))


def test_cron_step_syntax():
    sched = CronSchedule("0 */6 * * *")
    t = sched.next_after(dt(2024, 1, 1, 0, 0))
    assert t == dt(2024, 1, 1, 6, 0)


# ---------------------------------------------------------------------------
# make_schedule
# ---------------------------------------------------------------------------


def test_make_schedule_interval():
    sched = make_schedule("every 1h")
    assert isinstance(sched, IntervalSchedule)


def test_make_schedule_cron():
    sched = make_schedule("0 3 * * *")
    assert isinstance(sched, CronSchedule)


def test_make_schedule_invalid():
    with pytest.raises(ValueError):
        make_schedule("not-a-schedule")


# ---------------------------------------------------------------------------
# JobScheduler
# ---------------------------------------------------------------------------


def test_scheduler_add_and_due():
    js = JobScheduler()
    js.add_job("pg", "every 1h")
    now = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)
    js.mark_ran("pg", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    due = js.due_jobs(now)
    assert "pg" in due


def test_scheduler_not_due_yet():
    js = JobScheduler()
    js.add_job("pg", "every 2h")
    last = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    js.mark_ran("pg", last)
    now = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)
    assert "pg" not in js.due_jobs(now)


def test_scheduler_try_acquire_release():
    js = JobScheduler()
    js.add_job("pg", "every 1h")
    assert js.try_acquire("pg") is True
    assert js.try_acquire("pg") is False  # already running
    js.release("pg")
    assert js.try_acquire("pg") is True
    js.release("pg")


def test_scheduler_seconds_until_next():
    js = JobScheduler()
    js.add_job("pg", "every 1h")
    last = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    js.mark_ran("pg", last)
    now = datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)
    secs = js.seconds_until_next(now)
    assert 1700 < secs <= 1800  # ~30 min remaining
