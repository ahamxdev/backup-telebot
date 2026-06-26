"""Tests for config.py — settings loading and validation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tgbot_backup.config import load_settings, _split_csv, _parse_bool, _parse_float


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

class TestSplitCsv:
    def test_commas(self):
        assert _split_csv("-100,200,-300") == ("-100", "200", "-300")

    def test_semicolons(self):
        assert _split_csv("-100;200") == ("-100", "200")

    def test_newlines(self):
        assert _split_csv("-100\n200") == ("-100", "200")

    def test_empty_string(self):
        assert _split_csv("") == ()

    def test_none(self):
        assert _split_csv(None) == ()

    def test_trims_whitespace(self):
        assert _split_csv(" -100 , 200 ") == ("-100", "200")


class TestParseBool:
    def test_true_values(self):
        for v in ("1", "true", "yes", "on", "True", "YES"):
            assert _parse_bool(v) is True

    def test_false_values(self):
        for v in ("0", "false", "no", "off", "False", "NO"):
            assert _parse_bool(v) is False

    def test_empty_uses_default(self):
        assert _parse_bool("", default=True) is True
        assert _parse_bool("", default=False) is False


# ---------------------------------------------------------------------------
# load_settings
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def _set_required(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "-100111")
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))

    def test_minimal_valid(self, monkeypatch, tmp_path):
        self._set_required(monkeypatch, tmp_path)
        s = load_settings()
        assert s.telegram_bot_token == "123:fake"
        assert s.telegram_target_chat_ids == ("-100111",)
        assert s.watch_dir == str(tmp_path)

    def test_missing_token_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "-100")
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            load_settings()

    def test_missing_chat_ids_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))
        monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
        with pytest.raises(ValueError, match="TELEGRAM_TARGET_CHAT_IDS"):
            load_settings()

    def test_missing_watch_dir_raises(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "-100")
        monkeypatch.delenv("WATCH_DIR", raising=False)
        with pytest.raises(ValueError, match="WATCH_DIR"):
            load_settings()

    def test_invalid_chat_id_format_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "not_a_number")
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="chat_id"):
            load_settings()

    def test_defaults(self, monkeypatch, tmp_path):
        self._set_required(monkeypatch, tmp_path)
        s = load_settings()
        assert s.stable_seconds == 20.0
        assert s.scan_interval == 5.0
        assert s.max_file_size_mb == 50
        assert s.startup_send_existing is False
        assert s.clear_webhook_on_start is True
        assert s.telegram_api_base_url == "https://api.telegram.org"
        assert s.backup_jobs_file == ""
        assert s.admin_chat_id == ""

    def test_env_file_loading(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
        monkeypatch.delenv("WATCH_DIR", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            f"TELEGRAM_BOT_TOKEN=456:env_token\n"
            f"TELEGRAM_TARGET_CHAT_IDS=-200\n"
            f"WATCH_DIR={tmp_path}\n"
        )
        env_file.chmod(0o600)

        s = load_settings(env_file=str(env_file))
        assert s.telegram_bot_token == "456:env_token"

    def test_system_env_overrides_env_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "system_token")
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "-100")
        monkeypatch.setenv("WATCH_DIR", str(tmp_path))

        env_file = tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=file_token\n")
        env_file.chmod(0o600)

        s = load_settings(env_file=str(env_file))
        assert s.telegram_bot_token == "system_token"  # system wins

    def test_multiple_chat_ids(self, monkeypatch, tmp_path):
        self._set_required(monkeypatch, tmp_path)
        monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "-100,-200,-300")
        s = load_settings()
        assert s.telegram_target_chat_ids == ("-100", "-200", "-300")

    def test_new_fields_have_defaults(self, monkeypatch, tmp_path):
        self._set_required(monkeypatch, tmp_path)
        s = load_settings()
        assert s.backup_jobs_file == ""
        assert s.admin_chat_id == ""
        assert s.heartbeat_interval_hours == 0.0
        assert s.alert_consecutive_failures == 3
        assert s.default_encrypt_tool == "age"

    def test_max_file_size_bytes(self, monkeypatch, tmp_path):
        self._set_required(monkeypatch, tmp_path)
        monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
        s = load_settings()
        assert s.max_file_size_bytes == 100 * 1024 * 1024
