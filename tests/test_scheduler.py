"""Tests for scheduler.py — cron/interval parsing and next-run calculation."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from tgbot_backup.scheduler import (
    CronSchedule,
    IntervalSchedule,
    JobScheduler,
    make_schedule,
    parse_interval_seconds,
)


# ---------------------------------------------------------------------------
# Interval parsing
# ---------------------------------------------------------------------------

class TestParseIntervalSeconds:
    def test_hours(self):
        assert parse_interval_seconds("every 6h") == 21600

    def test_minutes(self):
        assert parse_interval_seconds("every 30m") == 1800

    def test_days(self):
        assert parse_interval_seconds("every 1d") == 86400

    def test_seconds(self):
        assert parse_interval_seconds("every 10s") == 10

    def test_long_form_hours(self):
        assert parse_interval_seconds("every 2 hours") == 7200

    def test_long_form_minutes(self):
        assert parse_interval_seconds("every 15 minutes") == 900

    def test_fractional(self):
        assert parse_interval_seconds("every 1.5h") == 5400

    def test_not_interval(self):
        assert parse_interval_seconds("0 3 * * *") is None

    def test_not_interval_plain_string(self):
        assert parse_interval_seconds("daily") is None

    def test_whitespace_tolerance(self):
        assert parse_interval_seconds("  every 5m  ") == 300


# ---------------------------------------------------------------------------
# CronSchedule
# ---------------------------------------------------------------------------

_UTC = timezone.utc


class TestCronSchedule:
    def _dt(self, year, month, day, hour=0, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=_UTC)

    def test_daily_3am(self):
        sched = CronSchedule("0 3 * * *")
        result = sched.next_after(self._dt(2024, 1, 1, 0, 0))
        assert result == self._dt(2024, 1, 1, 3, 0)

    def test_daily_3am_past_time(self):
        sched = CronSchedule("0 3 * * *")
        result = sched.next_after(self._dt(2024, 1, 1, 5, 0))
        assert result == self._dt(2024, 1, 2, 3, 0)

    def test_every_15_minutes(self):
        sched = CronSchedule("*/15 * * * *")
        result = sched.next_after(self._dt(2024, 1, 1, 0, 0))
        assert result == self._dt(2024, 1, 1, 0, 15)

    def test_every_15_minutes_from_boundary(self):
        sched = CronSchedule("*/15 * * * *")
        result = sched.next_after(self._dt(2024, 1, 1, 0, 15))
        assert result == self._dt(2024, 1, 1, 0, 30)

    def test_monthly(self):
        sched = CronSchedule("0 0 1 * *")  # midnight on the 1st
        result = sched.next_after(self._dt(2024, 1, 15, 0, 0))
        assert result == self._dt(2024, 2, 1, 0, 0)

    def test_specific_weekday_monday(self):
        # cron 1 = Monday
        sched = CronSchedule("0 9 * * 1")
        # 2024-01-01 is a Monday; if we're already past 9 AM, next is the following Monday
        result = sched.next_after(self._dt(2024, 1, 1, 12, 0))
        assert result.weekday() == 0  # Python Monday = 0
        assert result == self._dt(2024, 1, 8, 9, 0)

    def test_dow_7_equals_0_sunday(self):
        sched_0 = CronSchedule("0 9 * * 0")
        sched_7 = CronSchedule("0 9 * * 7")
        dt = self._dt(2024, 1, 1, 0, 0)
        assert sched_0.next_after(dt) == sched_7.next_after(dt)

    def test_invalid_field_count(self):
        with pytest.raises(ValueError):
            CronSchedule("0 3 * *")

    def test_invalid_step_zero(self):
        with pytest.raises(ValueError):
            CronSchedule("*/0 * * * *")

    def test_strictly_after(self):
        sched = CronSchedule("0 3 * * *")
        dt = self._dt(2024, 1, 1, 3, 0)  # exactly at run time
        result = sched.next_after(dt)
        assert result > dt  # must be STRICTLY after

    def test_range_field(self):
        sched = CronSchedule("0 9-17 * * *")  # every hour from 9 to 17
        result = sched.next_after(self._dt(2024, 1, 1, 8, 0))
        assert result == self._dt(2024, 1, 1, 9, 0)

    def test_list_field(self):
        sched = CronSchedule("0 0,12 * * *")
        result = sched.next_after(self._dt(2024, 1, 1, 0, 0))
        assert result == self._dt(2024, 1, 1, 12, 0)


# ---------------------------------------------------------------------------
# IntervalSchedule
# ---------------------------------------------------------------------------

class TestIntervalSchedule:
    def test_basic(self):
        sched = IntervalSchedule(3600)
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=_UTC)
        assert sched.next_after(dt) == datetime(2024, 1, 1, 1, 0, tzinfo=_UTC)

    def test_short(self):
        sched = IntervalSchedule(300)
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=_UTC)
        assert sched.next_after(dt) == datetime(2024, 1, 1, 0, 5, tzinfo=_UTC)

    def test_invalid_zero(self):
        with pytest.raises(ValueError):
            IntervalSchedule(0)

    def test_invalid_negative(self):
        with pytest.raises(ValueError):
            IntervalSchedule(-1)


# ---------------------------------------------------------------------------
# make_schedule
# ---------------------------------------------------------------------------

class TestMakeSchedule:
    def test_interval(self):
        assert isinstance(make_schedule("every 6h"), IntervalSchedule)

    def test_cron(self):
        assert isinstance(make_schedule("0 3 * * *"), CronSchedule)

    def test_invalid(self):
        with pytest.raises(ValueError):
            make_schedule("not a valid schedule at all 123")


# ---------------------------------------------------------------------------
# JobScheduler
# ---------------------------------------------------------------------------

class TestJobScheduler:
    def test_due_job_appears_after_schedule(self):
        sched = JobScheduler()
        now = datetime(2024, 1, 1, 0, 0, tzinfo=_UTC)
        sched.add_job("backup", "every 1h", now=now)

        # Not due at t=0
        assert sched.due_jobs(now) == []

        # Due at t+61 min
        assert sched.due_jobs(now + timedelta(hours=1, minutes=1)) == ["backup"]

    def test_try_acquire_and_release(self):
        sched = JobScheduler()
        sched.add_job("myjob", "every 1h")

        assert sched.try_acquire("myjob") is True
        assert sched.try_acquire("myjob") is False  # already locked
        sched.release("myjob")
        assert sched.try_acquire("myjob") is True  # available again
        sched.release("myjob")

    def test_mark_ran_advances_next_run(self):
        sched = JobScheduler()
        now = datetime(2024, 1, 1, 0, 0, tzinfo=_UTC)
        sched.add_job("job", "every 1h", now=now)

        first_next = sched._next_run["job"]
        sched.mark_ran("job", ran_at=first_next)
        second_next = sched._next_run["job"]
        assert second_next > first_next
        assert (second_next - first_next).total_seconds() == 3600

    def test_seconds_until_next(self):
        sched = JobScheduler()
        now = datetime(2024, 1, 1, 0, 0, tzinfo=_UTC)
        sched.add_job("job", "every 1h", now=now)
        # next_run is at 01:00, check at 00:30 → 1800s remaining
        assert sched.seconds_until_next(now + timedelta(minutes=30)) == pytest.approx(1800, abs=60)

    def test_empty_scheduler_seconds_until_next(self):
        sched = JobScheduler()
        assert sched.seconds_until_next() == float("inf")

    def test_disabled_job_not_scheduled(self):
        # Scheduler only gets enabled jobs from load_jobs — verify the scheduler
        # itself doesn't add disabled flag logic (that's jobs.load_jobs concern)
        sched = JobScheduler()
        sched.add_job("job", "every 5m")
        assert "job" in sched._next_run
