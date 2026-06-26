"""Backup job definition, TOML config loading, and command execution."""

from __future__ import annotations

import dataclasses
import glob as _glob
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]  # tomli backport
    except ImportError:
        tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|key|credential|auth)", re.IGNORECASE
)
_OUTPUT_TAIL_MAX = 4096  # bytes of captured output kept for error notifications
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class JobResult:
    name: str
    success: bool
    duration_seconds: float
    output_paths: list[Path] = dataclasses.field(default_factory=list)
    error_message: str | None = None
    output_tail: str = ""


@dataclasses.dataclass
class BackupJob:
    name: str
    command: str | list[str]
    schedule: str           # cron expr or "every Xh"; may be empty for manual-only jobs
    output: str
    enabled: bool = True
    target_chat_ids: tuple[str, ...] | None = None
    working_dir: str | None = None
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    timeout_seconds: float = 3600.0
    compress: bool = False
    encrypt_recipient: str = ""
    enforce_encryption: bool = False
    split_size_mb: int = 45
    retention_keep: int = 0
    sudo_prefix: bool = False
    # --- Command-interface fields ---
    # SECURITY: only manual_trigger=true jobs can be triggered from Telegram.
    # The command string always comes from this server-side config, never from Telegram.
    manual_trigger: bool = False
    # --- Caption metadata ---
    dbname: str = ""          # database name shown in Telegram caption
    restore_hint: str = ""    # per-job restore guide appended to caption


# ---------------------------------------------------------------------------
# Config-file permission check
# ---------------------------------------------------------------------------


def check_file_permissions(path: str) -> None:
    """Raise PermissionError if *path* is group- or world-writable.

    A writable jobs.toml lets anyone on the system inject shell commands that
    run as the bot user — effectively a privilege escalation vector.
    """
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return
    if mode & 0o022:
        raise PermissionError(
            f"Config file {path!r} is group- or world-writable (mode {oct(mode)}).\n"
            "This allows command injection via the jobs config.\n"
            f"Fix with:  chmod 600 {path}"
        )


# ---------------------------------------------------------------------------
# TOML loader
# ---------------------------------------------------------------------------


def load_jobs(jobs_file: str) -> list[BackupJob]:
    """Parse *jobs_file* (TOML) and return enabled jobs.

    Requires Python 3.11+ (built-in tomllib) or the 'tomli' backport.
    """
    if tomllib is None:
        raise RuntimeError(
            "TOML support requires Python 3.11+ or the 'tomli' backport.\n"
            "Install with:  pip install tomli"
        )

    check_file_permissions(jobs_file)

    try:
        with open(jobs_file, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise FileNotFoundError(f"Jobs config not found: {jobs_file!r}")
    except Exception as exc:
        raise ValueError(f"Failed to parse {jobs_file!r}: {exc}") from exc

    raw_list: list[dict[str, Any]] = data.get("job", [])
    if isinstance(raw_list, dict):
        raw_list = [raw_list]

    jobs: list[BackupJob] = []
    seen: set[str] = set()
    for raw in raw_list:
        job = _parse_job(raw)
        if job.name in seen:
            raise ValueError(f"Duplicate job name: {job.name!r}")
        seen.add(job.name)
        jobs.append(job)

    enabled = [j for j in jobs if j.enabled]
    scheduled = [j for j in enabled if j.schedule]
    manual_only = [j for j in enabled if not j.schedule and j.manual_trigger]
    logger.info(
        "Loaded %d job(s) from %s (%d enabled: %d scheduled, %d manual-only).",
        len(jobs), jobs_file, len(enabled), len(scheduled), len(manual_only),
    )
    return enabled


def _parse_job(raw: dict[str, Any]) -> BackupJob:
    name: str = str(raw.get("name", ""))
    if not _VALID_NAME_RE.match(name):
        raise ValueError(
            f"Job name {name!r} is invalid. Use letters, digits, underscores, and hyphens only."
        )

    command = raw.get("command")
    if not command:
        raise ValueError(f"Job {name!r}: 'command' is required.")
    if not isinstance(command, (str, list)):
        raise ValueError(f"Job {name!r}: 'command' must be a string or list of strings.")
    if isinstance(command, list):
        command = [str(c) for c in command]

    schedule: str = str(raw.get("schedule", "")).strip()
    # schedule may be empty for manual_trigger-only jobs (no auto-scheduling)
    manual_trigger_flag = bool(raw.get("manual_trigger", False))
    if not schedule and not manual_trigger_flag:
        raise ValueError(
            f"Job {name!r}: 'schedule' is required unless 'manual_trigger = true'."
        )

    output: str = str(raw.get("output", "")).strip()
    if not output:
        raise ValueError(f"Job {name!r}: 'output' is required.")

    chat_ids: tuple[str, ...] | None = None
    raw_ids = raw.get("target_chat_ids")
    if raw_ids is not None:
        if isinstance(raw_ids, str):
            parts = [p.strip() for p in re.split(r"[,;،\n]", raw_ids) if p.strip()]
        elif isinstance(raw_ids, list):
            parts = [str(x).strip() for x in raw_ids if str(x).strip()]
        else:
            parts = []
        for cid in parts:
            if not re.match(r"^-?\d+$", cid):
                raise ValueError(f"Job {name!r}: invalid chat_id {cid!r} (must be numeric).")
        chat_ids = tuple(parts)

    working_dir = raw.get("working_dir")
    if working_dir is not None:
        working_dir = str(working_dir)
        if not Path(working_dir).is_absolute():
            raise ValueError(f"Job {name!r}: 'working_dir' must be an absolute path.")

    raw_env = raw.get("env", {})
    if not isinstance(raw_env, dict):
        raise ValueError(f"Job {name!r}: 'env' must be a table.")
    env = {str(k): str(v) for k, v in raw_env.items()}

    return BackupJob(
        name=name,
        command=command,
        schedule=schedule,
        output=output,
        enabled=bool(raw.get("enabled", True)),
        target_chat_ids=chat_ids,
        working_dir=working_dir,
        env=env,
        timeout_seconds=float(raw.get("timeout_seconds", 3600.0)),
        compress=bool(raw.get("compress", False)),
        encrypt_recipient=str(raw.get("encrypt_recipient", "")),
        enforce_encryption=bool(raw.get("enforce_encryption", False)),
        split_size_mb=int(raw.get("split_size_mb", 45)),
        retention_keep=int(raw.get("retention_keep", 0)),
        sudo_prefix=bool(raw.get("sudo_prefix", False)),
        manual_trigger=bool(raw.get("manual_trigger", False)),
        dbname=str(raw.get("dbname", "")),
        restore_hint=str(raw.get("restore_hint", "")),
    )


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


def _masked_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if _SENSITIVE_KEY_RE.search(k) else v) for k, v in env.items()}


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _build_command(job: BackupJob) -> tuple[str | list[str], bool]:
    """Return (command, use_shell).

    shell=True is accepted only when the command is a string (may need pipes/redirects).
    If sudo_prefix is set and the command is a list, 'sudo' is prepended.
    Note: sudo requires NoNewPrivileges=false in the systemd unit.
    """
    cmd: str | list[str] = job.command
    if job.sudo_prefix:
        if isinstance(cmd, list):
            cmd = ["sudo"] + cmd
        else:
            cmd = "sudo " + cmd
    return cmd, isinstance(cmd, str)


def run_job(job: BackupJob) -> JobResult:
    """Execute *job* and return a JobResult.

    On success the result contains the list of output files produced.
    On failure it contains the error message and captured output tail.
    """
    start = time.monotonic()

    env = os.environ.copy()
    env.update(job.env)

    cmd, use_shell = _build_command(job)
    logger.info(
        "Job %r starting (timeout=%.0fs, shell=%s, sudo=%s).",
        job.name, job.timeout_seconds, use_shell, job.sudo_prefix,
    )
    logger.debug(
        "Job %r env overrides: %s", job.name, _masked_env(job.env)
    )

    try:
        proc = subprocess.run(
            cmd,
            shell=use_shell,
            capture_output=True,
            cwd=job.working_dir,
            env=env,
            timeout=job.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        msg = f"Job {job.name!r} timed out after {job.timeout_seconds:.0f}s"
        logger.error(msg)
        return JobResult(name=job.name, success=False, duration_seconds=elapsed, error_message=msg)
    except Exception as exc:
        elapsed = time.monotonic() - start
        msg = f"Job {job.name!r} failed to launch: {exc}"
        logger.error(msg)
        return JobResult(name=job.name, success=False, duration_seconds=elapsed, error_message=msg)

    elapsed = time.monotonic() - start
    combined = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    tail = combined[-_OUTPUT_TAIL_MAX:]

    if proc.returncode != 0:
        msg = f"Job {job.name!r} exited {proc.returncode}"
        logger.error("%s\nOutput tail:\n%s", msg, tail)
        return JobResult(
            name=job.name,
            success=False,
            duration_seconds=elapsed,
            error_message=msg,
            output_tail=tail,
        )

    output_paths = _find_outputs(job)
    if not output_paths:
        msg = f"Job {job.name!r} succeeded but no output matched {job.output!r}"
        logger.error(msg)
        return JobResult(
            name=job.name,
            success=False,
            duration_seconds=elapsed,
            error_message=msg,
            output_tail=tail,
        )

    logger.info(
        "Job %r done in %.1fs — %d output file(s).", job.name, elapsed, len(output_paths)
    )
    return JobResult(
        name=job.name,
        success=True,
        duration_seconds=elapsed,
        output_paths=output_paths,
        output_tail=tail,
    )


def _find_outputs(job: BackupJob) -> list[Path]:
    """Expand *job.output* (supports {name} and {date} placeholders + glob)."""
    today = date.today().strftime("%Y-%m-%d")
    pattern = job.output.format(name=job.name, date=today)
    matches = _glob.glob(pattern)
    if not matches:
        return []
    return sorted(
        (Path(p) for p in matches if Path(p).is_file()),
        key=lambda p: p.stat().st_mtime,
    )
