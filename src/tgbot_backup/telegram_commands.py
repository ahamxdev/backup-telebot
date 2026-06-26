"""Telegram command handler — optional two-way command interface for admins.

SECURITY DESIGN:
  • The command interface is DISABLED by default. It activates ONLY when
    TELEGRAM_ADMIN_CHAT_IDS is set to at least one numeric chat/user ID.
  • Only whitelisted admin IDs can send commands. Any message from any other
    ID is silently dropped after a warning log. No error reply is sent to
    unknown senders (no information leakage).
  • The fixed command vocabulary is: /list, /run, /status, /stop.
    Unknown commands receive a help reply only to admins.
  • /run only works for jobs explicitly marked 'manual_trigger = true' in
    jobs.toml. The command string to execute ALWAYS comes from the server-side
    jobs.toml — Telegram input can only supply a job name, never a shell command.
  • Rate limiting: at most COMMAND_RATE_LIMIT commands per admin per minute.
  • Polling runs in a separate daemon thread; it does not block the upload loop.
  • TLS verification is always on.
  • The existing SOCKS proxy setting applies to all Telegram traffic.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Any

logger = logging.getLogger(__name__)

# Commands the bot accepts — anything else gets a "unrecognized" reply
_KNOWN_COMMANDS = {"/list", "/run", "/status", "/stop", "/help"}

# Maximum number of updates fetched per long-poll request
_POLL_TIMEOUT = 25  # seconds for each getUpdates long-poll


class RateLimiter:
    """Sliding-window rate limiter: at most *limit* calls per *window* seconds per key."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self._limit = limit
        self._window = window
        self._history: dict[str, deque[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        history = self._history.setdefault(key, deque())
        # Drop timestamps outside the window
        while history and now - history[0] >= self._window:
            history.popleft()
        if len(history) >= self._limit:
            return False
        history.append(now)
        return True


class CommandHandler:
    """Long-polls Telegram for updates and dispatches admin commands.

    Args:
        client:        TelegramBotClient instance (shares session with service).
        settings:      Loaded Settings object.
        jobs:          List of loaded BackupJob objects.
        scheduler:     JobScheduler instance (may be None if no jobs configured).
        execute_job_fn: Callable(job) → runs the job in a background thread.
    """

    def __init__(
        self,
        client: Any,
        settings: Any,
        jobs: list[Any],
        scheduler: Any | None,
        execute_job_fn: Callable[[Any], None],
    ) -> None:
        self._client = client
        self._s = settings
        self._jobs = {j.name: j for j in jobs}
        self._scheduler = scheduler
        self._execute_job = execute_job_fn
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._rate_limiter = RateLimiter(limit=settings.command_rate_limit)
        self._update_offset: int | None = None
        # Track which schedules were added dynamically via /run every <interval>
        self._dynamic_schedules: set[str] = set()

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling thread if the command interface is enabled."""
        if not self._s.commands_enabled:
            logger.info(
                "TELEGRAM_ADMIN_CHAT_IDS is not set — command interface is DISABLED. "
                "Bot operates in one-way mode."
            )
            return

        admin_list = ", ".join(self._s.telegram_admin_chat_ids)
        logger.info(
            "Command interface ENABLED. Whitelisted admin IDs: %s. "
            "Only predefined manual_trigger=true jobs can be triggered from Telegram.",
            admin_list,
        )
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-cmd-poll",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=_POLL_TIMEOUT + 5)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        logger.debug("Command polling thread started.")
        while not self._stop_event.is_set():
            try:
                updates = self._client.get_updates(
                    offset=self._update_offset,
                    timeout=_POLL_TIMEOUT,
                )
                for update in updates:
                    uid: int = update["update_id"]
                    self._update_offset = uid + 1
                    try:
                        self._handle_update(update)
                    except Exception:
                        logger.exception("Error handling update %d", uid)
            except Exception as exc:
                logger.warning("getUpdates error: %s — retrying in 10s.", exc)
                time.sleep(10)

        logger.debug("Command polling thread stopped.")

    # ------------------------------------------------------------------
    # Update handler
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return  # ignore non-message updates (callbacks, etc.)

        chat_id = str(msg.get("chat", {}).get("id", ""))
        text: str = (msg.get("text") or "").strip()
        from_id = str(msg.get("from", {}).get("id", ""))

        if not text.startswith("/"):
            return  # only handle slash commands

        # --- Whitelist check ---
        if not self._is_admin(chat_id) and not self._is_admin(from_id):
            logger.warning(
                "Command from non-admin chat=%s user=%s — ignored.", chat_id, from_id
            )
            return  # do NOT reply to unknown senders

        admin_id = chat_id  # use chat_id for replies and rate limiting

        # --- Rate limit ---
        if not self._rate_limiter.is_allowed(admin_id):
            logger.warning("Rate limit hit for admin %s — dropping command.", admin_id)
            self._reply(admin_id, "Rate limit exceeded. Please slow down.")
            return

        # Log accepted command (never log message content beyond command)
        cmd_word = text.split()[0].lower().split("@")[0]  # strip @botname suffix
        logger.info("Command %r from admin %s", cmd_word, admin_id)

        # --- Dispatch ---
        if cmd_word == "/list":
            self._cmd_list(admin_id)
        elif cmd_word == "/run":
            self._cmd_run(admin_id, text)
        elif cmd_word == "/status":
            self._cmd_status(admin_id)
        elif cmd_word == "/stop":
            self._cmd_stop(admin_id, text)
        elif cmd_word in ("/help", "/start"):
            self._cmd_help(admin_id)
        else:
            self._reply(
                admin_id,
                f"Unknown command: {cmd_word}\nUse /help to see available commands.",
            )

    # ------------------------------------------------------------------
    # Individual commands
    # ------------------------------------------------------------------

    def _cmd_list(self, admin_id: str) -> None:
        manual_jobs = [j for j in self._jobs.values() if j.manual_trigger]
        if not manual_jobs:
            self._reply(admin_id, "No manual-trigger jobs are configured.")
            return
        lines = ["Available jobs (manual_trigger=true):"]
        for j in manual_jobs:
            sched = f"schedule: {j.schedule}" if j.schedule else "manual only"
            lines.append(f"  /run {j.name}  [{sched}]")
        self._reply(admin_id, "\n".join(lines))

    def _cmd_run(self, admin_id: str, text: str) -> None:
        # Syntax: /run <jobname> [every <interval>]
        # SECURITY: only the job name comes from Telegram. The command to execute
        # always comes from the server-side jobs.toml.
        parts = text.split()
        if len(parts) < 2:
            self._reply(admin_id, "Usage: /run <jobname> [every <interval>]")
            return

        job_name = parts[1]
        job = self._jobs.get(job_name)

        if job is None:
            self._reply(admin_id, f"Unknown job: {job_name!r}\nUse /list to see available jobs.")
            return

        if not job.manual_trigger:
            self._reply(
                admin_id,
                f"Job {job_name!r} is not marked manual_trigger=true and cannot be triggered from Telegram.",
            )
            return

        # Check for "every <interval>" suffix
        if len(parts) >= 4 and parts[2].lower() == "every":
            interval_expr = f"every {parts[3]}"
            self._register_dynamic_schedule(admin_id, job, interval_expr)
        else:
            # One-shot run
            self._reply(admin_id, f"Starting job {job_name!r}...")
            threading.Thread(
                target=self._execute_job,
                args=(job,),
                name=f"cmd-job-{job_name}",
                daemon=True,
            ).start()

    def _register_dynamic_schedule(self, admin_id: str, job: Any, interval_expr: str) -> None:
        from .scheduler import parse_interval_seconds, IntervalSchedule
        secs = parse_interval_seconds(interval_expr)
        if secs is None or secs <= 0:
            self._reply(admin_id, f"Invalid interval: {interval_expr!r}\nExample: /run {job.name} every 30m")
            return

        if self._scheduler is None:
            self._reply(admin_id, "Scheduler is not initialized.")
            return

        try:
            self._scheduler.add_job(job.name, interval_expr)
            self._dynamic_schedules.add(job.name)
            self._reply(
                admin_id,
                f"Job {job.name!r} scheduled: {interval_expr}\nUse /stop {job.name} to cancel.",
            )
        except Exception as exc:
            self._reply(admin_id, f"Failed to schedule {job.name!r}: {exc}")

    def _cmd_stop(self, admin_id: str, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self._reply(admin_id, "Usage: /stop <jobname>")
            return

        job_name = parts[1]
        if job_name not in self._dynamic_schedules:
            self._reply(
                admin_id,
                f"Job {job_name!r} has no dynamic schedule set via /run.\n"
                "(Static schedules from jobs.toml cannot be stopped at runtime.)",
            )
            return

        if self._scheduler and job_name in self._scheduler._next_run:
            # Remove from scheduler tracking
            self._scheduler._next_run.pop(job_name, None)
            self._scheduler._schedules.pop(job_name, None)
            self._scheduler._locks.pop(job_name, None)
            self._dynamic_schedules.discard(job_name)
            self._reply(admin_id, f"Dynamic schedule for {job_name!r} cancelled.")
        else:
            self._dynamic_schedules.discard(job_name)
            self._reply(admin_id, f"Job {job_name!r} was not actively scheduled.")

    def _cmd_status(self, admin_id: str) -> None:
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc)
        lines = [f"Status at {now.strftime('%Y-%m-%d %H:%M UTC')}"]

        if self._scheduler and self._scheduler._next_run:
            lines.append("\nScheduled jobs:")
            for name, t in sorted(self._scheduler._next_run.items()):
                delta = max(0, int((t - now).total_seconds()))
                lines.append(f"  {name}: next run in {_fmt_delta(delta)}")
        else:
            lines.append("No scheduled jobs.")

        if self._dynamic_schedules:
            lines.append(f"\nDynamic (from /run): {', '.join(sorted(self._dynamic_schedules))}")

        self._reply(admin_id, "\n".join(lines))

    def _cmd_help(self, admin_id: str) -> None:
        self._reply(
            admin_id,
            "Commands:\n"
            "  /list                      — list manual-trigger jobs\n"
            "  /run <job>                 — run a job immediately\n"
            "  /run <job> every <intv>    — schedule a job (e.g. every 30m)\n"
            "  /stop <job>                — cancel a dynamic schedule\n"
            "  /status                    — show scheduler status",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_admin(self, chat_id: str) -> bool:
        return chat_id in self._s.telegram_admin_chat_ids

    def _reply(self, chat_id: str, text: str) -> None:
        try:
            self._client.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Failed to send reply to %s: %s", chat_id, exc)


def _fmt_delta(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    h, rem = divmod(seconds, 3600)
    return f"{h}h {rem // 60}m"
