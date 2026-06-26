"""Logic layer — the core of the bot.

Classes:
  FileStabilityTracker  — waits until a file stops changing before declaring it ready.
  SingleInstanceLock    — fcntl-based mutex so only one service instance runs at a time.
  BackupSenderService   — the main watch-and-upload loop with crash-safe state machine,
                          optional scheduled backup jobs, and admin notifications.

Upload state machine (sidecar files next to each backup):
  backup.tar.gz                     — original file, waiting for stability
  backup.tar.gz.uploading           — atomically renamed; upload in progress
  backup.tar.gz.uploading.progress  — one chat_id per line: already-confirmed sends
  backup.tar.gz.uploading.sentok    — created after ALL chats confirmed; trigger cleanup
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .telegram_api import TelegramBotClient, TelegramAPIError

logger = logging.getLogger(__name__)

_UPLOADING_SUFFIX = ".uploading"
_SENTOK_SUFFIX = ".uploading.sentok"
_PROGRESS_SUFFIX = ".uploading.progress"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _silent_remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _size_human(size_bytes: int) -> str:
    value: float = size_bytes
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


# ---------------------------------------------------------------------------
# FileStabilityTracker
# ---------------------------------------------------------------------------


class FileStabilityTracker:
    """Holds off on declaring a file 'ready' until it hasn't changed for *stable_seconds*."""

    def __init__(self, stable_seconds: float) -> None:
        self._stable_seconds = stable_seconds
        self._state: dict[str, tuple[int, int, float]] = {}

    def is_ready(self, path: str) -> bool:
        try:
            st = os.stat(path)
        except OSError:
            self._state.pop(path, None)
            return False

        size, mtime_ns, now = st.st_size, st.st_mtime_ns, time.monotonic()

        if path not in self._state:
            self._state[path] = (size, mtime_ns, now)
            return False

        prev_size, prev_mtime_ns, last_change = self._state[path]
        if size != prev_size or mtime_ns != prev_mtime_ns:
            self._state[path] = (size, mtime_ns, now)
            return False

        return (now - last_change) >= self._stable_seconds

    def prune(self, existing_paths: set[str]) -> None:
        for path in list(self._state):
            if path not in existing_paths:
                del self._state[path]


# ---------------------------------------------------------------------------
# SingleInstanceLock
# ---------------------------------------------------------------------------


class SingleInstanceLock:
    """Prevents two bot instances from running simultaneously via fcntl.flock."""

    def __init__(self, lock_file: str) -> None:
        self._lock_file = lock_file
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self._fh = open(self._lock_file, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            logger.error(
                "Another instance is already running (lock file: %s). Exiting.",
                self._lock_file,
            )
            raise SystemExit(1)
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        logger.info("Acquired instance lock (pid=%d): %s", os.getpid(), self._lock_file)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        _silent_remove(self._lock_file)


# ---------------------------------------------------------------------------
# BackupSenderService
# ---------------------------------------------------------------------------


class BackupSenderService:
    """Main service: watches a directory, runs scheduled jobs, uploads files to Telegram."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = TelegramBotClient(
            token=settings.telegram_bot_token,
            socks_proxy=settings.socks_proxy,
            timeout=settings.request_timeout,
            api_base_url=settings.telegram_api_base_url,
        )
        self._tracker = FileStabilityTracker(settings.stable_seconds)
        self._stop = False
        self._startup_ignore: set[str] = set()
        self._public_ip: str | None = None
        self._public_ip_fetched = False

        # Jobs / scheduler (lazily populated in run())
        self._scheduler: Any | None = None
        self._jobs: list[Any] = []
        self._job_threads: dict[str, threading.Thread] = {}
        self._failure_counts: dict[str, int] = {}
        self._last_heartbeat: float = 0.0

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %s — will stop after the current cycle.", signum)
        self._stop = True

    # ------------------------------------------------------------------
    # Public IP (cached, best-effort)
    # ------------------------------------------------------------------

    def _get_public_ip(self) -> str:
        if self._public_ip_fetched:
            return self._public_ip or "unknown"
        self._public_ip_fetched = True
        try:
            resp = requests.get("https://api.ipify.org", timeout=5)
            self._public_ip = resp.text.strip()
        except Exception:
            self._public_ip = "unknown"
        return self._public_ip

    # ------------------------------------------------------------------
    # Caption builders
    # ------------------------------------------------------------------

    def _build_caption(
        self,
        file_path: str,
        original_name: str,
        *,
        checksum: str | None = None,
        part_info: str | None = None,
    ) -> str:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = 0
        caption = self._s.caption_template.format(
            filename=original_name,
            size_human=_size_human(size),
            hostname=socket.gethostname(),
            public_ip=self._get_public_ip(),
        )
        if checksum:
            caption += f"\nSHA-256: {checksum[:16]}..."
        if part_info:
            caption += f"\n{part_info}"
        return caption

    # ------------------------------------------------------------------
    # Progress / state-file helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _progress_path(uploading_path: str) -> str:
        return uploading_path + ".progress"

    @staticmethod
    def _sentok_path(uploading_path: str) -> str:
        return uploading_path + ".sentok"

    def _read_progress(self, uploading_path: str) -> set[str]:
        prog = self._progress_path(uploading_path)
        try:
            with open(prog, "r") as fh:
                return {line.strip() for line in fh if line.strip()}
        except FileNotFoundError:
            return set()

    def _record_progress(self, uploading_path: str, chat_id: str) -> None:
        prog = self._progress_path(uploading_path)
        with open(prog, "a") as fh:
            fh.write(chat_id + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _write_sentok(self, uploading_path: str) -> None:
        sentok = self._sentok_path(uploading_path)
        with open(sentok, "w") as fh:
            fh.write("ok\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------------
    # Core upload (single file → one or more chats)
    # ------------------------------------------------------------------

    def _send_to_all_chats(
        self,
        uploading_path: str,
        original_name: str,
        *,
        chat_ids: tuple[str, ...] | None = None,
        checksum: str | None = None,
        part_info: str | None = None,
    ) -> None:
        """Upload *uploading_path* to every chat in *chat_ids* (or the global default).

        Progress is persisted after each chat so a crash can be resumed.
        """
        effective_chat_ids = chat_ids if chat_ids is not None else self._s.telegram_target_chat_ids
        already_sent = self._read_progress(uploading_path)
        caption = self._build_caption(
            uploading_path, original_name, checksum=checksum, part_info=part_info
        )

        for chat_id in effective_chat_ids:
            if chat_id in already_sent:
                logger.info(
                    "  chat %s already confirmed for %s — skipping.", chat_id, original_name
                )
                continue
            logger.info("  Sending %s → chat %s …", original_name, chat_id)
            self._send_with_retry(uploading_path, original_name, chat_id, caption)
            self._record_progress(uploading_path, chat_id)
            logger.info("  Sent %s → chat %s (progress saved).", original_name, chat_id)

    def _send_with_retry(
        self,
        uploading_path: str,
        original_name: str,
        chat_id: str,
        caption: str,
    ) -> None:
        while True:
            try:
                self._client.send_document(
                    chat_id=chat_id,
                    file_path=uploading_path,
                    caption=caption,
                    filename=original_name,
                )
                return
            except TelegramAPIError as exc:
                if exc.error_code == 429:
                    retry_after = 30
                    m = re.search(r"retry after (\d+)", exc.description or "", re.IGNORECASE)
                    if m:
                        retry_after = int(m.group(1))
                    logger.warning(
                        "Rate-limited by Telegram (chat %s). Sleeping %ds …",
                        chat_id, retry_after,
                    )
                    time.sleep(retry_after)
                    continue
                raise

    def _complete_upload(self, uploading_path: str, original_name: str) -> None:
        self._write_sentok(uploading_path)
        _silent_remove(self._progress_path(uploading_path))
        _silent_remove(uploading_path)
        _silent_remove(self._sentok_path(uploading_path))
        logger.info("Deleted %s after confirmed upload to all chats.", original_name)

    # ------------------------------------------------------------------
    # Uploading-file handler (watch-dir mode)
    # ------------------------------------------------------------------

    def _handle_uploading_file(self, uploading_path: str) -> None:
        """Drive an upload (from watch-dir) to completion, resuming after crashes."""
        original_name = Path(uploading_path).name.removesuffix(_UPLOADING_SUFFIX)
        sentok = self._sentok_path(uploading_path)

        if os.path.exists(sentok):
            logger.info(
                "Found .sentok for %s — all sends confirmed; finishing cleanup.", original_name
            )
            _silent_remove(uploading_path)
            _silent_remove(sentok)
            _silent_remove(self._progress_path(uploading_path))
            return

        self._send_to_all_chats(uploading_path, original_name)
        self._complete_upload(uploading_path, original_name)

    # ------------------------------------------------------------------
    # Direct upload (job output — skips stability check)
    # ------------------------------------------------------------------

    def _upload_job_output(
        self,
        file_path: str,
        original_name: str,
        chat_ids: tuple[str, ...],
        checksum: str | None = None,
        part_info: str | None = None,
    ) -> None:
        """Upload a job-produced file through the state machine, bypassing stability."""
        uploading_path = file_path + _UPLOADING_SUFFIX
        try:
            os.rename(file_path, uploading_path)
        except OSError as exc:
            logger.warning("Could not claim %s (rename failed): %s", original_name, exc)
            return
        logger.info("Claimed job output %s → %s", original_name, Path(uploading_path).name)
        self._send_to_all_chats(
            uploading_path, original_name,
            chat_ids=chat_ids, checksum=checksum, part_info=part_info,
        )
        self._complete_upload(uploading_path, original_name)

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _prepare_startup_ignore(self) -> None:
        if self._s.startup_send_existing:
            return
        watch = Path(self._s.watch_dir)
        for p in watch.glob(self._s.file_glob):
            if p.is_file() and not p.name.endswith(_UPLOADING_SUFFIX):
                self._startup_ignore.add(str(p))
        if self._startup_ignore:
            logger.info(
                "startup_send_existing=false — %d pre-existing file(s) will be ignored.",
                len(self._startup_ignore),
            )

    def _recover_uploading_files(self) -> None:
        watch = Path(self._s.watch_dir)
        stale_threshold_s = self._s.delete_uploading_older_than_hours * 3600

        candidates = sorted(
            watch.glob(f"*{_UPLOADING_SUFFIX}"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        for p in candidates:
            if not p.is_file():
                continue
            if stale_threshold_s > 0:
                age_s = time.time() - p.stat().st_mtime
                if age_s > stale_threshold_s:
                    logger.warning(
                        "Stale .uploading file %s (age %.1fh > limit %.1fh) — removing.",
                        p.name, age_s / 3600, self._s.delete_uploading_older_than_hours,
                    )
                    _silent_remove(self._progress_path(str(p)))
                    _silent_remove(self._sentok_path(str(p)))
                    _silent_remove(str(p))
                    continue
            logger.info("Recovering interrupted upload: %s", p.name)
            try:
                self._handle_uploading_file(str(p))
            except Exception:
                logger.exception("Failed to recover %s; will retry next cycle.", p.name)

    # ------------------------------------------------------------------
    # New-file processing (watch-dir mode)
    # ------------------------------------------------------------------

    def _process_new_file(self, file_path: str) -> None:
        original_name = Path(file_path).name
        try:
            size = os.path.getsize(file_path)
        except OSError as exc:
            logger.warning("Cannot stat %s: %s — skipping.", original_name, exc)
            return

        if size > self._s.max_file_size_bytes:
            logger.warning(
                "File %s is %.1f MB, exceeds MAX_FILE_SIZE_MB=%d — skipping.",
                original_name, size / (1024 * 1024), self._s.max_file_size_mb,
            )
            return

        uploading_path = file_path + _UPLOADING_SUFFIX
        try:
            os.rename(file_path, uploading_path)
        except OSError as exc:
            logger.warning("Could not claim %s (rename failed): %s", original_name, exc)
            return

        logger.info("Claimed %s → %s", original_name, Path(uploading_path).name)
        self._send_to_all_chats(uploading_path, original_name)
        self._complete_upload(uploading_path, original_name)

    # ------------------------------------------------------------------
    # Watch-dir cycle
    # ------------------------------------------------------------------

    def _process_cycle(self) -> bool:
        watch = Path(self._s.watch_dir)
        processed = False

        for p in sorted(
            watch.glob(f"*{_UPLOADING_SUFFIX}"),
            key=lambda x: x.stat().st_mtime if x.exists() else 0,
        ):
            if p.is_file():
                try:
                    self._handle_uploading_file(str(p))
                    processed = True
                except Exception:
                    logger.exception("Error handling %s.", p.name)

        try:
            candidates = sorted(
                [
                    p
                    for p in watch.glob(self._s.file_glob)
                    if p.is_file()
                    and not p.name.endswith(_UPLOADING_SUFFIX)
                    and not p.name.endswith(".sentok")
                    and not p.name.endswith(".progress")
                ],
                key=lambda x: x.stat().st_mtime,
            )
        except OSError as exc:
            logger.error("Cannot list watch directory %s: %s", self._s.watch_dir, exc)
            return processed

        self._tracker.prune({str(p) for p in candidates})

        for p in candidates:
            path_str = str(p)
            if path_str in self._startup_ignore:
                continue
            if not self._tracker.is_ready(path_str):
                continue
            try:
                self._process_new_file(path_str)
                processed = True
            except TelegramAPIError:
                raise
            except Exception:
                logger.exception("Unexpected error processing %s.", p.name)

        return processed

    # ------------------------------------------------------------------
    # Jobs system
    # ------------------------------------------------------------------

    def _setup_jobs(self) -> None:
        if not self._s.backup_jobs_file:
            return

        from .jobs import load_jobs
        from .scheduler import JobScheduler

        try:
            self._jobs = load_jobs(self._s.backup_jobs_file)
        except Exception as exc:
            logger.error("Failed to load jobs from %s: %s", self._s.backup_jobs_file, exc)
            raise SystemExit(1)

        if not self._jobs:
            logger.info("No enabled jobs found in %s.", self._s.backup_jobs_file)
            return

        self._scheduler = JobScheduler()
        for job in self._jobs:
            try:
                self._scheduler.add_job(job.name, job.schedule)
            except ValueError as exc:
                logger.error("Bad schedule for job %r: %s", job.name, exc)
                raise SystemExit(1)

    def _run_due_jobs(self) -> bool:
        if self._scheduler is None:
            return False

        now = datetime.now(tz=timezone.utc)
        due = self._scheduler.due_jobs(now)
        if not due:
            return False

        job_map = {j.name: j for j in self._jobs}
        for name in due:
            if not self._scheduler.try_acquire(name):
                logger.info("Job %r is still running; skipping this cycle.", name)
                continue

            job = job_map.get(name)
            if job is None:
                self._scheduler.release(name)
                continue

            self._scheduler.mark_ran(name, now)
            t = threading.Thread(
                target=self._execute_job,
                args=(job,),
                name=f"job-{name}",
                daemon=True,
            )
            t.start()
            self._job_threads[name] = t

        return True

    def _execute_job(self, job: Any) -> None:
        """Run one job, process its output through the pipeline, and upload."""
        from .jobs import run_job
        from .pipeline import process_file
        from .notify import send_job_status, send_alert

        try:
            result = run_job(job)
        finally:
            if self._scheduler is not None:
                self._scheduler.release(job.name)

        if result.success:
            self._failure_counts[job.name] = 0
        else:
            self._failure_counts[job.name] = self._failure_counts.get(job.name, 0) + 1

        if not result.success or not result.output_paths:
            if self._s.admin_chat_id:
                send_job_status(self._client, self._s.admin_chat_id, result)
                count = self._failure_counts.get(job.name, 0)
                if self._s.alert_consecutive_failures > 0 and count >= self._s.alert_consecutive_failures:
                    send_alert(self._client, self._s.admin_chat_id, job.name, count)
            return

        chat_ids = job.target_chat_ids or self._s.telegram_target_chat_ids

        for output_path in result.output_paths:
            try:
                parts, checksum = process_file(
                    output_path,
                    compress=job.compress,
                    encrypt_recipient=job.encrypt_recipient,
                    encrypt_tool=self._s.default_encrypt_tool,
                    enforce_encryption=job.enforce_encryption,
                    split_size_mb=job.split_size_mb,
                )
            except Exception as exc:
                logger.error("Pipeline failed for job %r (%s): %s", job.name, output_path, exc)
                if self._s.admin_chat_id:
                    from .jobs import JobResult
                    fake = JobResult(
                        name=job.name,
                        success=False,
                        duration_seconds=result.duration_seconds,
                        error_message=f"Pipeline error: {exc}",
                    )
                    send_job_status(self._client, self._s.admin_chat_id, fake)
                continue

            total = len(parts)
            for i, part in enumerate(parts, 1):
                part_info = f"Part {i}/{total}" if total > 1 else None
                if total > 1:
                    part_info = (
                        f"Part {i}/{total}  "
                        f"(reassemble: cat {output_path.name}.part* > {output_path.name})"
                    )
                try:
                    self._upload_job_output(
                        str(part),
                        part.name,
                        chat_ids,
                        checksum=checksum if i == 1 else None,
                        part_info=part_info,
                    )
                except Exception as exc:
                    logger.error("Upload failed for %s: %s", part.name, exc)

        if self._s.admin_chat_id:
            send_job_status(
                self._client,
                self._s.admin_chat_id,
                result,
                filename=result.output_paths[0].name if result.output_paths else None,
            )

        # Retention: keep last N versions by mtime
        if job.retention_keep > 0:
            self._apply_retention(job)

    def _apply_retention(self, job: Any) -> None:
        from .jobs import _find_outputs
        all_outputs = _find_outputs(job)
        to_delete = all_outputs[: max(0, len(all_outputs) - job.retention_keep)]
        for p in to_delete:
            try:
                p.unlink()
                logger.info("Retention: deleted old output %s", p.name)
            except OSError as exc:
                logger.warning("Retention: could not delete %s: %s", p.name, exc)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _maybe_send_heartbeat(self) -> None:
        if not self._s.admin_chat_id or self._s.heartbeat_interval_hours <= 0:
            return
        interval_s = self._s.heartbeat_interval_hours * 3600
        if time.monotonic() - self._last_heartbeat >= interval_s:
            from .notify import send_heartbeat
            active = [n for n, t in self._job_threads.items() if t.is_alive()]
            send_heartbeat(self._client, self._s.admin_chat_id, active_jobs=active)
            self._last_heartbeat = time.monotonic()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        with SingleInstanceLock(self._s.lock_file):
            self._setup_jobs()
            self._prepare_startup_ignore()
            self._recover_uploading_files()

            if self._s.clear_webhook_on_start:
                try:
                    self._client.delete_webhook()
                except TelegramAPIError:
                    logger.warning("deleteWebhook failed; continuing without clearing.")

            logger.info(
                "Watching %s (glob=%r, stable=%.0fs, scan=%.0fs).%s",
                self._s.watch_dir,
                self._s.file_glob,
                self._s.stable_seconds,
                self._s.scan_interval,
                f"  Jobs: {len(self._jobs)}" if self._jobs else "",
            )

            while not self._stop:
                try:
                    file_processed = self._process_cycle()
                    self._run_due_jobs()
                    self._maybe_send_heartbeat()
                    if not file_processed:
                        # Sleep the shorter of scan_interval and time-to-next-job
                        if self._scheduler is not None:
                            sleep_s = min(
                                self._s.scan_interval,
                                self._scheduler.seconds_until_next(),
                            )
                        else:
                            sleep_s = self._s.scan_interval
                        time.sleep(max(0.1, sleep_s))
                except TelegramAPIError as exc:
                    logger.error(
                        "Telegram API error: %s — backing off %.0fs.",
                        exc, self._s.retry_backoff,
                    )
                    time.sleep(self._s.retry_backoff)
                except Exception:
                    logger.exception(
                        "Unexpected error — backing off %.0fs.", self._s.retry_backoff
                    )
                    time.sleep(self._s.retry_backoff)

        logger.info("Shutdown complete.")
