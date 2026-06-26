"""Tests for jobs.py — BackupJob loading and execution."""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tgbot_backup.jobs import (
    BackupJob,
    JobResult,
    check_file_permissions,
    load_jobs,
    run_job,
    _masked_env,
)


# ---------------------------------------------------------------------------
# check_file_permissions
# ---------------------------------------------------------------------------


def test_check_file_permissions_ok(tmp_path):
    f = tmp_path / "ok.toml"
    f.write_text("hi")
    f.chmod(0o600)
    check_file_permissions(str(f))  # should not raise


def test_check_file_permissions_group_writable_raises(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text("hi")
    f.chmod(0o664)
    with pytest.raises(PermissionError, match="group- or world-writable"):
        check_file_permissions(str(f))


def test_check_file_permissions_world_writable_raises(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text("hi")
    f.chmod(0o666)
    with pytest.raises(PermissionError):
        check_file_permissions(str(f))


def test_check_file_permissions_missing_ok(tmp_path):
    check_file_permissions(str(tmp_path / "nonexistent.toml"))  # no error


# ---------------------------------------------------------------------------
# load_jobs — TOML parsing
# ---------------------------------------------------------------------------


def _write_toml(tmp_path, content: str) -> str:
    f = tmp_path / "jobs.toml"
    f.write_text(textwrap.dedent(content))
    f.chmod(0o600)
    return str(f)


def test_load_jobs_minimal(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name    = "test-job"
        command = "echo hello"
        schedule = "every 1h"
        output   = "/tmp/test-*.txt"
        """,
    )
    jobs = load_jobs(path)
    assert len(jobs) == 1
    assert jobs[0].name == "test-job"
    assert jobs[0].schedule == "every 1h"


def test_load_jobs_disabled_excluded(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name    = "active"
        command = "echo"
        schedule = "every 1h"
        output   = "/tmp/x"

        [[job]]
        name    = "inactive"
        command = "echo"
        schedule = "every 1h"
        output   = "/tmp/x"
        enabled  = false
        """,
    )
    jobs = load_jobs(path)
    assert len(jobs) == 1
    assert jobs[0].name == "active"


def test_load_jobs_manual_trigger_no_schedule(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name           = "manual-job"
        command        = "echo hello"
        output         = "/tmp/x"
        manual_trigger = true
        """,
    )
    jobs = load_jobs(path)
    assert jobs[0].manual_trigger is True
    assert jobs[0].schedule == ""


def test_load_jobs_missing_schedule_raises(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name    = "bad-job"
        command = "echo"
        output  = "/tmp/x"
        """,
    )
    with pytest.raises(ValueError, match="schedule"):
        load_jobs(path)


def test_load_jobs_invalid_name_raises(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name    = "bad name!"
        command = "echo"
        schedule = "every 1h"
        output  = "/tmp/x"
        """,
    )
    with pytest.raises(ValueError, match="invalid"):
        load_jobs(path)


def test_load_jobs_duplicate_name_raises(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name    = "same"
        command = "echo"
        schedule = "every 1h"
        output  = "/tmp/x"

        [[job]]
        name    = "same"
        command = "echo"
        schedule = "every 1h"
        output  = "/tmp/x"
        """,
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_jobs(path)


def test_load_jobs_dbname_and_restore_hint(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [[job]]
        name         = "pg-job"
        command      = "echo"
        schedule     = "every 6h"
        output       = "/tmp/x"
        dbname       = "mydb"
        restore_hint = "Restore {dbname} from {filename}"
        """,
    )
    jobs = load_jobs(path)
    assert jobs[0].dbname == "mydb"
    assert "{dbname}" in jobs[0].restore_hint


def test_load_jobs_group_writable_raises(tmp_path):
    path = _write_toml(tmp_path, "[[job]]\nname='x'\ncommand='echo'\nschedule='every 1h'\noutput='/tmp/x'\n")
    os.chmod(path, 0o664)
    with pytest.raises(PermissionError):
        load_jobs(path)


# ---------------------------------------------------------------------------
# _masked_env
# ---------------------------------------------------------------------------


def test_masked_env_masks_password():
    env = {"PASSWORD": "secret", "DB_HOST": "localhost"}
    masked = _masked_env(env)
    assert masked["PASSWORD"] == "***"
    assert masked["DB_HOST"] == "localhost"


def test_masked_env_masks_token():
    env = {"API_TOKEN": "abc", "OTHER": "val"}
    masked = _masked_env(env)
    assert masked["API_TOKEN"] == "***"
    assert masked["OTHER"] == "val"


# ---------------------------------------------------------------------------
# run_job
# ---------------------------------------------------------------------------


def test_run_job_success(tmp_path):
    out = tmp_path / "out.txt"
    job = BackupJob(
        name="echo-job",
        command=f"echo hello > {out}",
        schedule="every 1h",
        output=str(out),
    )
    result = run_job(job)
    assert result.success
    assert out.exists()


def test_run_job_failure_nonzero_exit():
    job = BackupJob(
        name="fail-job",
        command="exit 1",
        schedule="every 1h",
        output="/tmp/nonexistent_*.txt",
    )
    result = run_job(job)
    assert not result.success
    assert "exited 1" in result.error_message


def test_run_job_no_output_is_failure(tmp_path):
    job = BackupJob(
        name="noout-job",
        command="true",
        schedule="every 1h",
        output=str(tmp_path / "*.nonexistent"),
    )
    result = run_job(job)
    assert not result.success
    assert "no output" in (result.error_message or "").lower()


def test_run_job_timeout():
    job = BackupJob(
        name="slow-job",
        command="sleep 10",
        schedule="every 1h",
        output="/tmp/x",
        timeout_seconds=0.01,
    )
    result = run_job(job)
    assert not result.success
    assert "timed out" in (result.error_message or "").lower()
