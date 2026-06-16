"""Logic layer — the core of the bot.

Three classes:
  FileStabilityTracker  — waits until a file stops changing before declaring it ready.
  SingleInstanceLock    — fcntl-based mutex so only one service instance runs at a time.
  BackupSenderService   — the main watch-and-upload loop with crash-safe state machine.

State machine files (all sibling to the original backup file):
  backup.tar.gz                     — original file, present while waiting for stability
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
import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .telegram_api import TelegramBotClient, TelegramAPIError

logger = logging.getLogger(__name__)

# File-suffix constants for the upload state machine
_UPLOADING_SUFFIX = ".uploading"
_SENTOK_SUFFIX = ".uploading.sentok"
_PROGRESS_SUFFIX = ".uploading.progress"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _silent_remove(path: str) -> None:
    """Delete *path*, ignoring errors if it doesn't exist."""
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
    """Holds off on declaring a file 'ready' until it hasn't changed for *stable_seconds*.

    Uses time.monotonic() so wall-clock adjustments don't reset the counter.
    """

    def __init__(self, stable_seconds: float) -> None:
        self._stable_seconds = stable_seconds
        # path -> (size, mtime_ns, last_change_monotonic)
        self._state: dict[str, tuple[int, int, float]] = {}

    def is_ready(self, path: str) -> bool:
        try:
            st = os.stat(path)
        except OSError:
            self._state.pop(path, None)
            return False

        size = st.st_size
        mtime_ns = st.st_mtime_ns
        now = time.monotonic()

        if path not in self._state:
            self._state[path] = (size, mtime_ns, now)
            return False

        prev_size, prev_mtime_ns, last_change = self._state[path]
        if size != prev_size or mtime_ns != prev_mtime_ns:
            self._state[path] = (size, mtime_ns, now)
            return False

        return (now - last_change) >= self._stable_seconds

    def prune(self, existing_paths: set[str]) -> None:
        """Drop tracking entries for paths no longer present (free memory)."""
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
    """Main service: scans *watch_dir*, uploads new backup files to Telegram, deletes them."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = TelegramBotClient(
            token=settings.telegram_bot_token,
            socks_proxy=settings.socks_proxy,
            timeout=settings.request_timeout,
        )
        self._tracker = FileStabilityTracker(settings.stable_seconds)
        self._stop = False
        self._startup_ignore: set[str] = set()
        self._public_ip: str | None = None
        self._public_ip_fetched = False

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %s — will stop after the current cycle.", signum)
        self._stop = True

    # ------------------------------------------------------------------
    # Public IP (cached, best-effort; never crashes the service)
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
    # Caption builder
    # ------------------------------------------------------------------

    def _build_caption(self, file_path: str, original_name: str) -> str:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = 0
        return self._s.caption_template.format(
            filename=original_name,
            size_human=_size_human(size),
            hostname=socket.gethostname(),
            public_ip=self._get_public_ip(),
        )

    # ------------------------------------------------------------------
    # Progress / state-file helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _progress_path(uploading_path: str) -> str:
        return uploading_path + ".progress"

    @staticmethod
    def _sentok_path(uploading_path: str) -> str:
        # backup.tar.gz.uploading  →  backup.tar.gz.uploading.sentok
        return uploading_path + ".sentok"

    def _read_progress(self, uploading_path: str) -> set[str]:
        prog = self._progress_path(uploading_path)
        try:
            with open(prog, "r") as fh:
                return {line.strip() for line in fh if line.strip()}
        except FileNotFoundError:
            return set()

    def _record_progress(self, uploading_path: str, chat_id: str) -> None:
        """Append *chat_id* to the progress file and fsync so it survives a crash."""
        prog = self._progress_path(uploading_path)
        with open(prog, "a") as fh:
            fh.write(chat_id + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _write_sentok(self, uploading_path: str) -> None:
        """Create the .sentok marker and fsync before any deletion."""
        sentok = self._sentok_path(uploading_path)
        with open(sentok, "w") as fh:
            fh.write("ok\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------------
    # Core upload (single file → all chats)
    # ------------------------------------------------------------------

    def _send_to_all_chats(self, uploading_path: str, original_name: str) -> None:
        """Send *uploading_path* to every configured chat, skipping already-sent ones.

        Per-chat progress is persisted to disk after each successful send so a crash
        can be resumed without re-uploading to chats that already received the file.
        """
        already_sent = self._read_progress(uploading_path)
        caption = self._build_caption(uploading_path, original_name)

        for chat_id in self._s.telegram_target_chat_ids:
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
        """Call send_document, handling Telegram rate-limit (429) by sleeping and retrying."""
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
                    m = re.search(
                        r"retry after (\d+)", exc.description or "", re.IGNORECASE
                    )
                    if m:
                        retry_after = int(m.group(1))
                    logger.warning(
                        "Rate-limited by Telegram (chat %s). Sleeping %ds …",
                        chat_id,
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue
                raise

    def _complete_upload(self, uploading_path: str, original_name: str) -> None:
        """All chats confirmed: write sentok → delete progress → delete payload → delete sentok."""
        self._write_sentok(uploading_path)

        prog = self._progress_path(uploading_path)
        _silent_remove(prog)

        _silent_remove(uploading_path)

        sentok = self._sentok_path(uploading_path)
        _silent_remove(sentok)

        logger.info("Deleted %s after confirmed upload to all chats.", original_name)

    # ------------------------------------------------------------------
    # Uploading-file handler (new or recovered)
    # ------------------------------------------------------------------

    def _handle_uploading_file(self, uploading_path: str) -> None:
        """Drive an upload to completion, whether fresh or resumed after a crash.

        Crash recovery rules:
          .sentok present   → all sends confirmed, only cleanup remains
          .sentok absent    → use .progress to find which chats still need sending
        """
        # Strip the .uploading suffix to recover the original display name
        original_name = Path(uploading_path).name.removesuffix(_UPLOADING_SUFFIX)
        sentok = self._sentok_path(uploading_path)

        if os.path.exists(sentok):
            logger.info(
                "Found .sentok for %s — all sends confirmed; finishing cleanup.",
                original_name,
            )
            _silent_remove(uploading_path)
            _silent_remove(sentok)
            _silent_remove(self._progress_path(uploading_path))
            return

        self._send_to_all_chats(uploading_path, original_name)
        self._complete_upload(uploading_path, original_name)

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _prepare_startup_ignore(self) -> None:
        """Record pre-existing files so they are not sent when startup_send_existing=false."""
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
        """On startup, resume any .uploading files left by a previous crash."""
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
                        p.name,
                        age_s / 3600,
                        self._s.delete_uploading_older_than_hours,
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
    # New-file processing (called from the main cycle)
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
                original_name,
                size / (1024 * 1024),
                self._s.max_file_size_mb,
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
    # Main cycle
    # ------------------------------------------------------------------

    def _process_cycle(self) -> bool:
        """Scan the watch directory once and upload any ready files.

        Returns True if at least one file was processed (caller skips the scan sleep).
        """
        watch = Path(self._s.watch_dir)
        processed = False

        # 1. Handle any leftover .uploading files first
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

        # 2. Gather normal candidate files
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
                # Let caller catch and sleep with retry_backoff
                raise
            except Exception:
                logger.exception("Unexpected error processing %s.", p.name)

        return processed

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        with SingleInstanceLock(self._s.lock_file):
            self._prepare_startup_ignore()
            self._recover_uploading_files()

            if self._s.clear_webhook_on_start:
                try:
                    self._client.delete_webhook()
                except TelegramAPIError:
                    logger.warning("deleteWebhook failed; continuing without clearing.")

            logger.info(
                "Watching %s (glob=%r, stable=%.0fs, scan=%.0fs).",
                self._s.watch_dir,
                self._s.file_glob,
                self._s.stable_seconds,
                self._s.scan_interval,
            )

            while not self._stop:
                try:
                    processed = self._process_cycle()
                    if not processed:
                        time.sleep(self._s.scan_interval)
                except TelegramAPIError as exc:
                    logger.error(
                        "Telegram API error: %s — backing off %.0fs.",
                        exc,
                        self._s.retry_backoff,
                    )
                    time.sleep(self._s.retry_backoff)
                except Exception:
                    logger.exception(
                        "Unexpected error — backing off %.0fs.", self._s.retry_backoff
                    )
                    time.sleep(self._s.retry_backoff)

        logger.info("Shutdown complete.")
