"""Tests for config.py."""

from __future__ import annotations

import os
import stat
import textwrap

import pytest

from tgbot_backup.config import load_settings, Settings


def _write_env(tmp_path, content: str) -> str:
    env_file = tmp_path / ".env"
    env_file.write_text(textwrap.dedent(content))
    env_file.chmod(0o600)
    return str(env_file)


def _base_env():
    return {
        "TELEGRAM_BOT_TOKEN": "999:AAtest",
        "TELEGRAM_TARGET_CHAT_IDS": "123",
        "WATCH_DIR": "/tmp",
    }


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


def test_missing_token_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
    monkeypatch.delenv("WATCH_DIR", raising=False)
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_settings()


def test_missing_chat_ids_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:AAtest")
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
    monkeypatch.delenv("WATCH_DIR", raising=False)
    with pytest.raises(ValueError, match="TELEGRAM_TARGET_CHAT_IDS"):
        load_settings()


def test_missing_watch_dir_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:AAtest")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "123")
    monkeypatch.delenv("WATCH_DIR", raising=False)
    with pytest.raises(ValueError, match="WATCH_DIR"):
        load_settings()


def test_invalid_chat_id_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:AAtest")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "notanumber")
    monkeypatch.setenv("WATCH_DIR", "/tmp")
    with pytest.raises(ValueError, match="notanumber"):
        load_settings()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_IDS", raising=False)
    monkeypatch.delenv("COMMAND_RATE_LIMIT", raising=False)
    s = load_settings()
    assert s.telegram_bot_token == "999:AAtest"
    assert s.telegram_target_chat_ids == ("123",)
    assert s.watch_dir == "/tmp"
    assert s.stable_seconds == 20.0
    assert s.scan_interval == 5.0
    assert s.max_file_size_mb == 50
    assert s.telegram_admin_chat_ids == ()
    assert s.command_rate_limit == 10
    assert not s.commands_enabled


def test_multiple_chat_ids(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "111,222,-333")
    s = load_settings()
    assert s.telegram_target_chat_ids == ("111", "222", "-333")


# ---------------------------------------------------------------------------
# .env file loading
# ---------------------------------------------------------------------------


def test_env_file_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
    monkeypatch.delenv("WATCH_DIR", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_IDS", raising=False)
    env_file = _write_env(
        tmp_path,
        """
        TELEGRAM_BOT_TOKEN=111:AAenv
        TELEGRAM_TARGET_CHAT_IDS=777
        WATCH_DIR=/tmp
        """,
    )
    s = load_settings(env_file=env_file)
    assert s.telegram_bot_token == "111:AAenv"


def test_env_file_system_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "system:token")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "123")
    monkeypatch.setenv("WATCH_DIR", "/tmp")
    env_file = _write_env(
        tmp_path,
        """
        TELEGRAM_BOT_TOKEN=file:token
        """,
    )
    s = load_settings(env_file=env_file)
    assert s.telegram_bot_token == "system:token"


# ---------------------------------------------------------------------------
# Admin chat IDs
# ---------------------------------------------------------------------------


def test_admin_chat_ids_parsed(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "100,200")
    s = load_settings()
    assert s.telegram_admin_chat_ids == ("100", "200")
    assert s.commands_enabled


def test_invalid_admin_chat_id_raises(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "notanumber")
    with pytest.raises(ValueError, match="notanumber"):
        load_settings()


def test_command_rate_limit_default(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("COMMAND_RATE_LIMIT", raising=False)
    s = load_settings()
    assert s.command_rate_limit == 10


def test_command_rate_limit_custom(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("COMMAND_RATE_LIMIT", "5")
    s = load_settings()
    assert s.command_rate_limit == 5
