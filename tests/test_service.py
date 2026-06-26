"""Tests for service.py — state machine, caption builder, upload flow."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from tgbot_backup.service import (
    BackupSenderService,
    FileStabilityTracker,
    _size_human,
)
from tgbot_backup.telegram_api import TelegramAPIError


# ---------------------------------------------------------------------------
# _size_human
# ---------------------------------------------------------------------------


def test_size_human_bytes():
    assert _size_human(512) == "512.0 B"


def test_size_human_kb():
    assert "KB" in _size_human(2048)


def test_size_human_mb():
    assert "MB" in _size_human(5 * 1024 * 1024)


# ---------------------------------------------------------------------------
# FileStabilityTracker
# ---------------------------------------------------------------------------


def test_stability_tracker_not_ready_on_first_scan(tmp_path):
    f = tmp_path / "file.tar.gz"
    f.write_bytes(b"data")
    tracker = FileStabilityTracker(stable_seconds=100)
    assert not tracker.is_ready(str(f))


def test_stability_tracker_ready_after_delay(tmp_path):
    import time
    f = tmp_path / "file.tar.gz"
    f.write_bytes(b"data")
    tracker = FileStabilityTracker(stable_seconds=0.0)
    tracker.is_ready(str(f))  # first scan
    time.sleep(0.05)
    assert tracker.is_ready(str(f))


def test_stability_tracker_resets_on_change(tmp_path):
    import time
    f = tmp_path / "file.tar.gz"
    f.write_bytes(b"data")
    tracker = FileStabilityTracker(stable_seconds=0.0)
    tracker.is_ready(str(f))
    time.sleep(0.05)
    f.write_bytes(b"more data")
    assert not tracker.is_ready(str(f))


def test_stability_tracker_prune(tmp_path):
    f = tmp_path / "file.tar.gz"
    f.write_bytes(b"data")
    tracker = FileStabilityTracker(stable_seconds=100)
    tracker.is_ready(str(f))
    tracker.prune(set())
    assert str(f) not in tracker._state


# ---------------------------------------------------------------------------
# _build_caption
# ---------------------------------------------------------------------------


def _make_service(fake_settings):
    with patch("tgbot_backup.service.TelegramBotClient"):
        svc = BackupSenderService(fake_settings)
    svc._public_ip = "1.2.3.4"
    svc._public_ip_fetched = True
    return svc


def test_build_caption_basic(tmp_path, fake_settings):
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x" * 1024)
    svc = _make_service(fake_settings)
    caption, follow_up = svc._build_caption(str(f), "backup.sql.gz")
    assert "backup.sql.gz" in caption
    assert follow_up == ""


def test_build_caption_fits_within_1024(tmp_path, fake_settings):
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x" * 1024)
    svc = _make_service(fake_settings)
    caption, _ = svc._build_caption(str(f), "backup.sql.gz", checksum="abc" * 20)
    assert len(caption) <= 1024


def test_build_caption_restore_hint_split(tmp_path, fake_settings):
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x" * 1024)
    svc = _make_service(fake_settings)
    long_hint = "Restore steps: " + ("step " * 300)
    caption, follow_up = svc._build_caption(
        str(f), "backup.sql.gz", restore_hint=long_hint
    )
    assert len(caption) <= 1024
    # hint moves to follow_up if it pushes caption over limit
    assert long_hint not in caption or follow_up != ""


def test_build_caption_no_credentials(tmp_path, fake_settings):
    """Caption must never contain DB credentials."""
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"data")
    svc = _make_service(fake_settings)
    caption, _ = svc._build_caption(
        str(f),
        "backup.sql.gz",
        dbname="mydb",
        restore_hint="Restore {dbname} from {filename}",
    )
    # The hint uses template vars, so no raw "password" or credential should appear
    assert "password" not in caption.lower()
    assert "passwd" not in caption.lower()


def test_build_caption_includes_dbname(tmp_path, fake_settings):
    """dbname and restore_hint {dbname} substitution work correctly."""
    import textwrap

    fake_settings_with_emoji = fake_settings.__class__(
        **{
            **fake_settings.__dict__,
            "caption_template": (
                "📦 {filename}\n"
                "🗄️ {dbname}\n"
                "📊 {size_human}\n"
                "🖥️ {hostname}\n"
                "🌐 {public_ip}\n"
                "📅 {timestamp}\n"
                "🔑 {sha256}"
            ),
        }
    )
    f = tmp_path / "backup.bak"
    f.write_bytes(b"data")
    svc = _make_service(fake_settings_with_emoji)
    caption, _ = svc._build_caption(
        str(f),
        "backup.bak",
        dbname="bigtools",
        checksum="abc123",
        restore_hint="Restore {dbname} from {filename}",
    )
    assert "bigtools" in caption


# ---------------------------------------------------------------------------
# Progress / state files
# ---------------------------------------------------------------------------


def test_read_progress_empty(tmp_path, fake_settings):
    svc = _make_service(fake_settings)
    uploading = str(tmp_path / "backup.sql.gz.uploading")
    assert svc._read_progress(uploading) == set()


def test_record_and_read_progress(tmp_path, fake_settings):
    svc = _make_service(fake_settings)
    uploading = str(tmp_path / "backup.sql.gz.uploading")
    # create dummy uploading file
    Path(uploading).write_bytes(b"")
    svc._record_progress(uploading, "111")
    svc._record_progress(uploading, "222")
    assert svc._read_progress(uploading) == {"111", "222"}


# ---------------------------------------------------------------------------
# Startup ignore
# ---------------------------------------------------------------------------


def test_startup_ignore_files_skipped(tmp_path, fake_settings):
    """Files present at startup should be ignored when startup_send_existing=False."""
    f = tmp_path / "old.tar.gz"
    f.write_bytes(b"old data")

    with patch("tgbot_backup.service.TelegramBotClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        svc = BackupSenderService(fake_settings)
        svc._startup_ignore = {str(f)}
        # simulate the tracker saying it's ready
        svc._tracker._state[str(f)] = (len(b"old data"), 0, 0)

        processed = svc._process_cycle()
        mock_client.send_document.assert_not_called()
