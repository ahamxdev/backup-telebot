"""Shared test fixtures."""

from __future__ import annotations

import pytest

from tgbot_backup.config import Settings


@pytest.fixture()
def fake_settings(tmp_path):
    return Settings(
        telegram_bot_token="123456:AABBCCtest",
        telegram_target_chat_ids=("111", "222"),
        watch_dir=str(tmp_path),
        file_glob="*",
        stable_seconds=0.1,
        scan_interval=0.1,
        retry_backoff=0.1,
        max_file_size_mb=50,
        startup_send_existing=False,
        clear_webhook_on_start=False,
        delete_uploading_older_than_hours=0.0,
        lock_file=str(tmp_path / "test.lock"),
        caption_template="File: {filename}\nSize: {size_human}\nServer: {hostname}\nPublic IP: {public_ip}",
        request_timeout=5.0,
        socks_proxy="",
        telegram_api_base_url="https://api.telegram.org",
        backup_jobs_file="",
        admin_chat_id="",
        heartbeat_interval_hours=0.0,
        alert_consecutive_failures=3,
        default_encrypt_tool="age",
        telegram_admin_chat_ids=(),
        command_rate_limit=10,
    )
