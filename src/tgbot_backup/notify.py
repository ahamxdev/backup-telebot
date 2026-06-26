"""Admin notifications: job status, heartbeat, and failure alerts.

All functions are best-effort: failures are logged but never raise so a
notification problem never crashes the main service loop.
"""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .jobs import JobResult
    from .telegram_api import TelegramBotClient

logger = logging.getLogger(__name__)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, mins = divmod(m, 60)
    return f"{h}h {mins}m"


def send_job_status(
    client: "TelegramBotClient",
    admin_chat_id: str,
    result: "JobResult",
    *,
    filename: str | None = None,
    checksum: str | None = None,
) -> None:
    """Send a job completion/failure notification to *admin_chat_id*."""
    status = "[OK]" if result.success else "[FAIL]"
    lines = [
        f"{status} {result.name}",
        f"Duration: {_fmt_duration(result.duration_seconds)}",
        f"Host: {socket.gethostname()}",
    ]
    if filename:
        lines.append(f"File: {filename}")
    if checksum:
        lines.append(f"SHA-256: {checksum[:16]}...")
    if not result.success and result.error_message:
        lines.append(f"Error: {result.error_message}")
    if not result.success and result.output_tail:
        tail_lines = result.output_tail[-400:].strip()
        if tail_lines:
            lines.append(f"Output:\n{tail_lines}")

    _send_text(client, admin_chat_id, "\n".join(lines))


def send_heartbeat(
    client: "TelegramBotClient",
    admin_chat_id: str,
    *,
    active_jobs: list[str] | None = None,
) -> None:
    """Send a service-alive heartbeat message."""
    lines = [
        "[HEARTBEAT] backup-telebot is alive",
        f"Host: {socket.gethostname()}",
    ]
    if active_jobs:
        lines.append(f"Active: {', '.join(active_jobs)}")
    _send_text(client, admin_chat_id, "\n".join(lines))


def send_alert(
    client: "TelegramBotClient",
    admin_chat_id: str,
    job_name: str,
    failure_count: int,
) -> None:
    """Send a consecutive-failure alert."""
    text = (
        f"[ALERT] {job_name}\n"
        f"Failed {failure_count} consecutive time(s).\n"
        f"Host: {socket.gethostname()}"
    )
    _send_text(client, admin_chat_id, text)


def _send_text(client: "TelegramBotClient", chat_id: str, text: str) -> None:
    try:
        client.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("Notification to chat %s failed: %s", chat_id, exc)
