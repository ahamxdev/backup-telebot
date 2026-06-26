"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make the src layout importable without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """A temporary directory cleaned up after the test."""
    return tmp_path


@pytest.fixture
def watch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "watch"
    d.mkdir()
    return d


@pytest.fixture
def fake_settings(watch_dir: Path, tmp_path: Path):
    """Minimal Settings-like object for service tests."""
    from tgbot_backup.config import Settings
    return Settings(
        telegram_bot_token="123:fake_token",
        telegram_target_chat_ids=("-100111", "-100222"),
        watch_dir=str(watch_dir),
        file_glob="*.tar.gz",
        stable_seconds=0.1,
        scan_interval=0.1,
        retry_backoff=1.0,
        max_file_size_mb=50,
        startup_send_existing=True,
        clear_webhook_on_start=False,
        delete_uploading_older_than_hours=0.0,
        lock_file=str(tmp_path / "test.lock"),
        caption_template="File: {filename}\nSize: {size_human}\nServer: {hostname}\nPublic IP: {public_ip}",
        request_timeout=10.0,
        socks_proxy="",
        telegram_api_base_url="https://api.telegram.org",
        backup_jobs_file="",
        admin_chat_id="",
        heartbeat_interval_hours=0.0,
        alert_consecutive_failures=3,
        default_encrypt_tool="age",
    )
