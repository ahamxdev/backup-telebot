"""Tests for telegram_commands.py — security and command dispatch."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from tgbot_backup.telegram_commands import CommandHandler, RateLimiter
from tgbot_backup.jobs import BackupJob


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_under_limit():
    rl = RateLimiter(limit=3, window=60.0)
    assert rl.is_allowed("user1")
    assert rl.is_allowed("user1")
    assert rl.is_allowed("user1")


def test_rate_limiter_blocks_over_limit():
    rl = RateLimiter(limit=2, window=60.0)
    rl.is_allowed("user1")
    rl.is_allowed("user1")
    assert not rl.is_allowed("user1")


def test_rate_limiter_independent_keys():
    rl = RateLimiter(limit=1, window=60.0)
    rl.is_allowed("user1")
    assert not rl.is_allowed("user1")
    assert rl.is_allowed("user2")  # separate key


def test_rate_limiter_window_expires():
    rl = RateLimiter(limit=1, window=0.05)
    rl.is_allowed("user1")
    assert not rl.is_allowed("user1")
    time.sleep(0.1)
    assert rl.is_allowed("user1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(name="pg-backup", manual_trigger=True):
    return BackupJob(
        name=name,
        command="echo hello",
        schedule="" if manual_trigger else "every 1h",
        output="/tmp/*.bak",
        manual_trigger=manual_trigger,
        dbname="mydb",
    )


def _make_settings(admin_ids=("100",), rate_limit=10):
    s = MagicMock()
    s.telegram_admin_chat_ids = tuple(admin_ids)
    s.command_rate_limit = rate_limit
    s.commands_enabled = len(admin_ids) > 0
    return s


def _make_handler(jobs=None, admin_ids=("100",), rate_limit=10):
    client = MagicMock()
    settings = _make_settings(admin_ids=admin_ids, rate_limit=rate_limit)
    execute_fn = MagicMock()
    handler = CommandHandler(
        client=client,
        settings=settings,
        jobs=jobs or [_make_job()],
        scheduler=None,
        execute_job_fn=execute_fn,
    )
    return handler, client, execute_fn


def _update(chat_id, text, from_id=None):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": int(chat_id)},
            "from": {"id": int(from_id or chat_id)},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Security: whitelist enforcement
# ---------------------------------------------------------------------------


def test_unknown_sender_silently_dropped():
    """Messages from non-admin senders must produce no reply and no execution."""
    handler, client, execute_fn = _make_handler(admin_ids=("100",))
    handler._handle_update(_update(chat_id="999", text="/list"))
    client.send_message.assert_not_called()
    execute_fn.assert_not_called()


def test_admin_sender_gets_reply():
    handler, client, execute_fn = _make_handler(admin_ids=("100",))
    handler._handle_update(_update(chat_id="100", text="/list"))
    client.send_message.assert_called_once()


def test_commands_disabled_when_no_admin_ids():
    settings = _make_settings(admin_ids=())
    client = MagicMock()
    handler = CommandHandler(
        client=client,
        settings=settings,
        jobs=[_make_job()],
        scheduler=None,
        execute_job_fn=MagicMock(),
    )
    handler.start()
    assert handler._thread is None  # thread not started


# ---------------------------------------------------------------------------
# Security: /run only for manual_trigger=true
# ---------------------------------------------------------------------------


def test_run_manual_trigger_job_executes(tmp_path):
    job = _make_job(manual_trigger=True)
    handler, client, execute_fn = _make_handler(jobs=[job])
    handler._handle_update(_update(chat_id="100", text="/run pg-backup"))
    time.sleep(0.1)  # thread starts
    execute_fn.assert_called_once_with(job)


def test_run_non_manual_trigger_job_rejected():
    """Jobs without manual_trigger=true must be refused even if admin asks."""
    job = _make_job(name="auto-job", manual_trigger=False)
    handler, client, execute_fn = _make_handler(jobs=[job])
    handler._handle_update(_update(chat_id="100", text="/run auto-job"))
    time.sleep(0.05)
    execute_fn.assert_not_called()
    # Should have sent an error message
    client.send_message.assert_called_once()
    reply_text = client.send_message.call_args[1].get("text", "")
    assert "manual_trigger" in reply_text or "cannot" in reply_text


def test_run_unknown_job_rejected():
    handler, client, execute_fn = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/run nonexistent-job"))
    time.sleep(0.05)
    execute_fn.assert_not_called()
    client.send_message.assert_called_once()


def test_run_no_job_name_rejected():
    handler, client, execute_fn = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/run"))
    execute_fn.assert_not_called()
    client.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Security: command string always from jobs.toml, never from Telegram
# ---------------------------------------------------------------------------


def test_run_uses_command_from_jobs_not_telegram():
    """The job command must come from the jobs map, not from the Telegram text."""
    malicious_job_name = "pg-backup"
    actual_job = _make_job(name=malicious_job_name, manual_trigger=True)
    handler, client, execute_fn = _make_handler(jobs=[actual_job])

    # Send /run with extra text that looks like an injection attempt
    handler._handle_update(
        _update(chat_id="100", text=f"/run {malicious_job_name} ; rm -rf /")
    )
    time.sleep(0.1)
    # execute_fn is called with the actual job object, not a constructed command
    if execute_fn.called:
        called_job = execute_fn.call_args[0][0]
        assert called_job is actual_job
        assert called_job.command == "echo hello"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_after_limit():
    handler, client, execute_fn = _make_handler(rate_limit=2)
    handler._handle_update(_update(chat_id="100", text="/list"))
    handler._handle_update(_update(chat_id="100", text="/list"))
    handler._handle_update(_update(chat_id="100", text="/list"))
    # third call should be rate-limited; client still called but with rate-limit message
    assert client.send_message.call_count >= 3
    last_call_text = client.send_message.call_args[1].get("text", "")
    # The rate-limit reply should mention limiting
    assert "Rate limit" in last_call_text or "slow down" in last_call_text.lower()


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------


def test_list_shows_manual_trigger_jobs():
    handler, client, _ = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/list"))
    reply = client.send_message.call_args[1].get("text", "")
    assert "pg-backup" in reply


def test_list_no_manual_jobs():
    job = _make_job(manual_trigger=False)
    handler, client, _ = _make_handler(jobs=[job])
    handler._handle_update(_update(chat_id="100", text="/list"))
    reply = client.send_message.call_args[1].get("text", "")
    assert "No manual" in reply


# ---------------------------------------------------------------------------
# /status and /help
# ---------------------------------------------------------------------------


def test_status_reply_received():
    handler, client, _ = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/status"))
    client.send_message.assert_called_once()


def test_help_reply_received():
    handler, client, _ = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/help"))
    reply = client.send_message.call_args[1].get("text", "")
    assert "/run" in reply
    assert "/list" in reply


def test_unknown_command_reply():
    handler, client, _ = _make_handler()
    handler._handle_update(_update(chat_id="100", text="/unknown"))
    reply = client.send_message.call_args[1].get("text", "")
    assert "Unknown" in reply or "/help" in reply
