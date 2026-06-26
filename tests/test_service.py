"""Tests for service.py — state machine, crash recovery, and backward compatibility."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from tgbot_backup.service import (
    BackupSenderService,
    FileStabilityTracker,
    SingleInstanceLock,
    _silent_remove,
    _size_human,
)
from tgbot_backup.telegram_api import TelegramAPIError


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

class TestSilentRemove:
    def test_removes_existing(self, tmp_path):
        f = tmp_path / "x"
        f.write_bytes(b"data")
        _silent_remove(str(f))
        assert not f.exists()

    def test_missing_file_no_error(self, tmp_path):
        _silent_remove(str(tmp_path / "nonexistent"))  # should not raise


class TestSizeHuman:
    def test_bytes(self):
        assert _size_human(512) == "512.0 B"

    def test_kilobytes(self):
        assert _size_human(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _size_human(5 * 1024 * 1024) == "5.0 MB"


# ---------------------------------------------------------------------------
# FileStabilityTracker
# ---------------------------------------------------------------------------

class TestFileStabilityTracker:
    def test_not_ready_on_first_check(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"x")
        tracker = FileStabilityTracker(0.1)
        assert tracker.is_ready(str(f)) is False

    def test_ready_after_stable_period(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"x")
        tracker = FileStabilityTracker(0.1)
        tracker.is_ready(str(f))  # register
        time.sleep(0.2)
        assert tracker.is_ready(str(f)) is True

    def test_not_ready_if_file_changes(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"x")
        tracker = FileStabilityTracker(0.5)
        tracker.is_ready(str(f))
        time.sleep(0.1)
        f.write_bytes(b"xy")  # changed
        time.sleep(0.1)
        assert tracker.is_ready(str(f)) is False  # reset

    def test_missing_file_returns_false(self, tmp_path):
        tracker = FileStabilityTracker(0.1)
        assert tracker.is_ready(str(tmp_path / "nonexistent")) is False

    def test_prune_removes_gone_paths(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"x")
        tracker = FileStabilityTracker(0.1)
        tracker.is_ready(str(f))
        assert str(f) in tracker._state
        tracker.prune(set())
        assert str(f) not in tracker._state


# ---------------------------------------------------------------------------
# State machine / crash recovery
# ---------------------------------------------------------------------------

class TestStateMachine:
    """Tests for the upload state machine in BackupSenderService."""

    def _make_service(self, fake_settings):
        svc = BackupSenderService(fake_settings)
        # Mock the Telegram client so no real network calls happen
        svc._client = MagicMock()
        svc._client.send_document.return_value = {"message_id": 1}
        svc._get_public_ip = MagicMock(return_value="1.2.3.4")
        return svc

    def test_complete_upload_flow(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        f = watch_dir / "backup.tar.gz"
        f.write_bytes(b"fake backup data")

        uploading = watch_dir / "backup.tar.gz.uploading"
        os.rename(str(f), str(uploading))

        svc._send_to_all_chats(str(uploading), "backup.tar.gz")
        svc._complete_upload(str(uploading), "backup.tar.gz")

        # File should be gone
        assert not uploading.exists()
        assert not (watch_dir / "backup.tar.gz.uploading.progress").exists()
        assert not (watch_dir / "backup.tar.gz.uploading.sentok").exists()

        # Should have sent to all configured chats
        calls = svc._client.send_document.call_args_list
        assert len(calls) == 2  # two chat IDs in fake_settings

    def test_progress_file_persists_chat_ids(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        uploading = watch_dir / "backup.tar.gz.uploading"
        uploading.write_bytes(b"data")

        svc._record_progress(str(uploading), "-100111")
        progress = Path(str(uploading) + ".progress")
        assert "-100111" in progress.read_text()

    def test_resume_after_crash_skips_already_sent(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        uploading = watch_dir / "backup.tar.gz.uploading"
        uploading.write_bytes(b"data")

        # Simulate: first chat already received the file (crash after sending to -100111)
        svc._record_progress(str(uploading), "-100111")

        svc._send_to_all_chats(str(uploading), "backup.tar.gz")

        # Only the second chat should have received a send call
        calls = svc._client.send_document.call_args_list
        sent_chats = [c.kwargs["chat_id"] if c.kwargs else c.args[0] for c in calls]
        # -100111 was already sent, so only -100222 should be called
        assert "-100111" not in sent_chats or sent_chats.count("-100111") == 0
        assert "-100222" in sent_chats or any("-100222" in str(c) for c in calls)

    def test_sentok_present_means_cleanup_only(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        uploading = watch_dir / "backup.tar.gz.uploading"
        uploading.write_bytes(b"data")
        sentok = watch_dir / "backup.tar.gz.uploading.sentok"
        sentok.write_text("ok\n")

        svc._handle_uploading_file(str(uploading))

        # No uploads should have been made
        svc._client.send_document.assert_not_called()
        # Both files should be gone
        assert not uploading.exists()
        assert not sentok.exists()

    def test_stale_uploading_file_abandoned(self, fake_settings, watch_dir):
        import tgbot_backup.config as cfg
        settings = cfg.Settings(
            **{
                **fake_settings.__dict__,
                "delete_uploading_older_than_hours": 0.0001,  # ~0.36 seconds
            }
        )
        svc = self._make_service(settings)

        old_file = watch_dir / "old_backup.tar.gz.uploading"
        old_file.write_bytes(b"data")
        # Make it appear old by manipulating mtime
        old_time = time.time() - 10  # 10 seconds ago
        os.utime(str(old_file), (old_time, old_time))

        svc._recover_uploading_files()

        assert not old_file.exists()
        svc._client.send_document.assert_not_called()

    def test_rate_limit_handling(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        uploading = watch_dir / "backup.tar.gz.uploading"
        uploading.write_bytes(b"data")

        # First call raises 429, second succeeds
        svc._client.send_document.side_effect = [
            TelegramAPIError("Too Many Requests: retry after 1", error_code=429, description="retry after 1"),
            {"message_id": 1},
            {"message_id": 2},
        ]

        with patch("time.sleep") as mock_sleep:
            svc._send_to_all_chats(str(uploading), "backup.tar.gz")
            mock_sleep.assert_called_with(1)  # slept for the retry_after value

    def test_backward_compat_no_jobs(self, fake_settings):
        """With no BACKUP_JOBS_FILE configured, service must run in watch-dir mode."""
        svc = self._make_service(fake_settings)
        assert fake_settings.backup_jobs_file == ""
        svc._setup_jobs()
        assert svc._scheduler is None
        assert svc._jobs == []

    def test_file_size_limit_enforced(self, fake_settings, watch_dir):
        svc = self._make_service(fake_settings)
        big_file = watch_dir / "big.tar.gz"
        # Write a file and then manually call _process_new_file with mocked size
        big_file.write_bytes(b"x")
        with patch("os.path.getsize", return_value=100 * 1024 * 1024):  # 100 MB > 50 MB limit
            svc._process_new_file(str(big_file))
        svc._client.send_document.assert_not_called()

    def test_startup_ignore_pre_existing_files(self, fake_settings, watch_dir):
        # Create a pre-existing file
        existing = watch_dir / "existing.tar.gz"
        existing.write_bytes(b"old backup")

        svc = self._make_service(fake_settings)
        # startup_send_existing=True in fake_settings, so nothing should be ignored
        svc._prepare_startup_ignore()
        assert str(existing) not in svc._startup_ignore

    def test_startup_ignore_when_disabled(self, fake_settings, watch_dir, tmp_path):
        import tgbot_backup.config as cfg
        settings = cfg.Settings(
            **{**fake_settings.__dict__, "startup_send_existing": False}
        )
        existing = watch_dir / "old.tar.gz"
        existing.write_bytes(b"old")

        svc = self._make_service(settings)
        svc._s = settings
        svc._prepare_startup_ignore()
        assert str(existing) in svc._startup_ignore

    def test_custom_chat_ids_for_upload(self, fake_settings, watch_dir):
        """_send_to_all_chats should use provided chat_ids override."""
        svc = self._make_service(fake_settings)
        uploading = watch_dir / "backup.tar.gz.uploading"
        uploading.write_bytes(b"data")

        override_ids = ("-999888",)
        svc._send_to_all_chats(
            str(uploading), "backup.tar.gz", chat_ids=override_ids
        )

        calls = svc._client.send_document.call_args_list
        assert len(calls) == 1
        assert "-999888" in str(calls[0])
