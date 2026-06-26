"""Tests for jobs.py — config loading, validation, execution, and permission checks."""
from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

from tgbot_backup.jobs import (
    BackupJob,
    JobResult,
    _find_outputs,
    _masked_env,
    check_file_permissions,
    load_jobs,
    run_job,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def write_toml(path: Path, content: str) -> Path:
    p = path / "jobs.toml"
    p.write_text(textwrap.dedent(content))
    p.chmod(0o600)
    return p


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

class TestCheckFilePermissions:
    def test_secure_mode_ok(self, tmp_path):
        f = tmp_path / "jobs.toml"
        f.write_text("")
        f.chmod(0o600)
        check_file_permissions(str(f))  # should not raise

    def test_group_writable_raises(self, tmp_path):
        f = tmp_path / "jobs.toml"
        f.write_text("")
        f.chmod(0o660)
        with pytest.raises(PermissionError, match="group- or world-writable"):
            check_file_permissions(str(f))

    def test_world_writable_raises(self, tmp_path):
        f = tmp_path / "jobs.toml"
        f.write_text("")
        f.chmod(0o666)
        with pytest.raises(PermissionError):
            check_file_permissions(str(f))

    def test_missing_file_ok(self, tmp_path):
        # Missing file should not raise (FileNotFoundError is the caller's concern)
        check_file_permissions(str(tmp_path / "nonexistent.toml"))


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------

class TestLoadJobs:
    def test_minimal_valid_job(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "pgdump"
            command = "pg_dump mydb > /tmp/backup.sql"
            schedule = "0 3 * * *"
            output = "/tmp/backup.sql"
        """)
        jobs = load_jobs(str(f))
        assert len(jobs) == 1
        assert jobs[0].name == "pgdump"

    def test_disabled_job_excluded(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "active"
            command = "echo hi"
            schedule = "every 1h"
            output = "/tmp/x"
            enabled = true

            [[job]]
            name = "inactive"
            command = "echo bye"
            schedule = "every 1h"
            output = "/tmp/y"
            enabled = false
        """)
        jobs = load_jobs(str(f))
        assert len(jobs) == 1
        assert jobs[0].name == "active"

    def test_duplicate_name_raises(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "dup"
            command = "echo a"
            schedule = "every 1h"
            output = "/tmp/a"

            [[job]]
            name = "dup"
            command = "echo b"
            schedule = "every 1h"
            output = "/tmp/b"
        """)
        with pytest.raises(ValueError, match="Duplicate"):
            load_jobs(str(f))

    def test_invalid_job_name_raises(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "bad name!"
            command = "echo"
            schedule = "every 1h"
            output = "/tmp/x"
        """)
        with pytest.raises(ValueError, match="invalid"):
            load_jobs(str(f))

    def test_missing_command_raises(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "job1"
            schedule = "every 1h"
            output = "/tmp/x"
        """)
        with pytest.raises(ValueError, match="command"):
            load_jobs(str(f))

    def test_invalid_chat_id_raises(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "job1"
            command = "echo hi"
            schedule = "every 1h"
            output = "/tmp/x"
            target_chat_ids = ["not_a_number"]
        """)
        with pytest.raises(ValueError, match="chat_id"):
            load_jobs(str(f))

    def test_relative_working_dir_raises(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "job1"
            command = "echo"
            schedule = "every 1h"
            output = "/tmp/x"
            working_dir = "relative/path"
        """)
        with pytest.raises(ValueError, match="absolute"):
            load_jobs(str(f))

    def test_list_command_loaded(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "myjob"
            command = ["pg_dump", "-U", "postgres", "mydb"]
            schedule = "every 6h"
            output = "/tmp/x.sql"
        """)
        jobs = load_jobs(str(f))
        assert isinstance(jobs[0].command, list)
        assert jobs[0].command[0] == "pg_dump"

    def test_env_table_loaded(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "job1"
            command = "echo"
            schedule = "every 1h"
            output = "/tmp/x"
            [job.env]
            MY_VAR = "hello"
        """)
        jobs = load_jobs(str(f))
        assert jobs[0].env == {"MY_VAR": "hello"}

    def test_group_writable_file_raises(self, tmp_path):
        f = tmp_path / "jobs.toml"
        f.write_text(textwrap.dedent("""
            [[job]]
            name = "x"
            command = "echo"
            schedule = "every 1h"
            output = "/tmp/x"
        """))
        f.chmod(0o664)
        with pytest.raises(PermissionError):
            load_jobs(str(f))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_jobs(str(tmp_path / "nonexistent.toml"))

    def test_all_optional_fields_defaults(self, tmp_path):
        f = write_toml(tmp_path, """
            [[job]]
            name = "minimal"
            command = "echo hi"
            schedule = "every 1h"
            output = "/tmp/x"
        """)
        jobs = load_jobs(str(f))
        j = jobs[0]
        assert j.enabled is True
        assert j.target_chat_ids is None
        assert j.working_dir is None
        assert j.env == {}
        assert j.timeout_seconds == 3600.0
        assert j.compress is False
        assert j.encrypt_recipient == ""
        assert j.enforce_encryption is False
        assert j.split_size_mb == 45
        assert j.retention_keep == 0
        assert j.sudo_prefix is False


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------

class TestMaskedEnv:
    def test_password_masked(self):
        result = _masked_env({"DB_PASSWORD": "s3cr3t", "HOST": "localhost"})
        assert result["DB_PASSWORD"] == "***"
        assert result["HOST"] == "localhost"

    def test_token_masked(self):
        result = _masked_env({"API_TOKEN": "abc123"})
        assert result["API_TOKEN"] == "***"

    def test_secret_masked(self):
        result = _masked_env({"AWS_SECRET_ACCESS_KEY": "xyz"})
        assert result["AWS_SECRET_ACCESS_KEY"] == "***"

    def test_normal_key_not_masked(self):
        result = _masked_env({"BACKUP_DIR": "/backups"})
        assert result["BACKUP_DIR"] == "/backups"


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

class TestRunJob:
    def _make_job(self, tmp_path, command, output_pattern=None):
        output = output_pattern or str(tmp_path / "output.txt")
        return BackupJob(
            name="testjob",
            command=command,
            schedule="every 1h",
            output=output,
        )

    def test_successful_job_string_command(self, tmp_path):
        out = tmp_path / "out.txt"
        job = self._make_job(
            tmp_path,
            f"echo hello > {out}",
            str(out),
        )
        result = run_job(job)
        assert result.success
        assert result.output_paths == [out]

    def test_successful_job_list_command(self, tmp_path):
        out = tmp_path / "out.txt"
        # Can't redirect with list command; write directly with Python
        job = BackupJob(
            name="testjob",
            command=["python3", "-c", f"open('{out}', 'w').write('hello')"],
            schedule="every 1h",
            output=str(out),
        )
        result = run_job(job)
        assert result.success
        assert len(result.output_paths) == 1

    def test_failed_job_nonzero_exit(self, tmp_path):
        job = self._make_job(tmp_path, "exit 1")
        result = run_job(job)
        assert not result.success
        assert "1" in (result.error_message or "")

    def test_timeout(self, tmp_path):
        job = BackupJob(
            name="slow",
            command="sleep 10",
            schedule="every 1h",
            output=str(tmp_path / "x"),
            timeout_seconds=0.1,
        )
        result = run_job(job)
        assert not result.success
        assert "timed out" in (result.error_message or "").lower()

    def test_missing_output_file(self, tmp_path):
        # Command succeeds but produces no output at the expected path
        job = self._make_job(tmp_path, "echo hello", str(tmp_path / "nonexistent.tar.gz"))
        result = run_job(job)
        assert not result.success
        assert "not found" in (result.error_message or "").lower() or "output" in (result.error_message or "").lower()

    def test_env_override(self, tmp_path):
        out = tmp_path / "out.txt"
        job = BackupJob(
            name="envjob",
            command=f"echo $MY_TEST_VAR > {out}",
            schedule="every 1h",
            output=str(out),
            env={"MY_TEST_VAR": "hello_env"},
        )
        result = run_job(job)
        assert result.success
        assert "hello_env" in out.read_text()

    def test_glob_output_pattern(self, tmp_path):
        # Job produces file with today's date in name; glob pattern
        out = tmp_path / "backup_test.tar.gz"
        job = BackupJob(
            name="globjob",
            command=f"touch {out}",
            schedule="every 1h",
            output=str(tmp_path / "backup_*.tar.gz"),
        )
        result = run_job(job)
        assert result.success
        assert len(result.output_paths) >= 1
