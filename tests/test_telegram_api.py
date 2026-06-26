"""Tests for telegram_api.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from tgbot_backup.telegram_api import (
    TelegramBotClient,
    TelegramAPIError,
    _mask_token_in_url,
    _mask_proxy_url,
)


# ---------------------------------------------------------------------------
# Token masking
# ---------------------------------------------------------------------------


def test_mask_token_standard_url():
    url = "https://api.telegram.org/bot123456:AABBtest/sendDocument"
    masked = _mask_token_in_url(url)
    assert "123456:AABBtest" not in masked
    assert "bot***" in masked
    assert "/sendDocument" in masked


def test_mask_token_custom_base():
    url = "http://localhost:8081/bot999:XXX/getUpdates"
    masked = _mask_token_in_url(url)
    assert "999:XXX" not in masked
    assert "***" in masked


def test_mask_proxy_url():
    proxy = "socks5h://user:pass@10.0.0.1:1080"
    masked = _mask_proxy_url(proxy)
    assert "user" not in masked
    assert "pass" not in masked
    assert "10.0.0.1:1080" in masked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client():
    return TelegramBotClient(
        token="123:AATESTtoken",
        timeout=5.0,
        api_base_url="https://api.telegram.org",
    )


def _ok_response(result: object) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"ok": True, "result": result}
    return mock


def _error_response(status: int, body: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.text = body
    mock.json.side_effect = ValueError("not json")
    return mock


def _api_error_response(code: int, description: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "ok": False,
        "error_code": code,
        "description": description,
    }
    return mock


# ---------------------------------------------------------------------------
# send_document
# ---------------------------------------------------------------------------


def test_send_document_success(tmp_path):
    client = _make_client()
    f = tmp_path / "backup.sql"
    f.write_bytes(b"data")
    with patch.object(client._session, "post", return_value=_ok_response({"message_id": 1})):
        result = client.send_document(chat_id="111", file_path=str(f), caption="test")
    assert result["message_id"] == 1


def test_send_document_token_not_in_error(tmp_path):
    client = _make_client()
    f = tmp_path / "backup.sql"
    f.write_bytes(b"data")
    resp = _error_response(500, "Internal Server Error")
    with patch.object(client._session, "post", return_value=resp):
        try:
            client.send_document(chat_id="111", file_path=str(f))
        except TelegramAPIError as exc:
            assert "AATESTtoken" not in str(exc)
        else:
            pytest.fail("Expected TelegramAPIError")


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_success():
    client = _make_client()
    with patch.object(client._session, "post", return_value=_ok_response({"message_id": 2})):
        result = client.send_message(chat_id="111", text="hello")
    assert result["message_id"] == 2


def test_send_message_truncates_long_text():
    client = _make_client()
    sent_data = {}

    def capture(url, data=None, files=None, timeout=None):
        sent_data.update(data or {})
        return _ok_response({})

    with patch.object(client._session, "post", side_effect=capture):
        client.send_message(chat_id="111", text="x" * 5000)

    assert len(sent_data.get("text", "")) <= 4096


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_connection_error_raises_telegram_api_error():
    client = _make_client()
    with patch.object(
        client._session,
        "post",
        side_effect=requests.exceptions.ConnectionError("refused"),
    ):
        with pytest.raises(TelegramAPIError, match="Connection error"):
            client.delete_webhook()


def test_connection_error_no_token_in_message():
    client = _make_client()
    with patch.object(
        client._session,
        "post",
        side_effect=requests.exceptions.ConnectionError("refused"),
    ):
        try:
            client.delete_webhook()
        except TelegramAPIError as exc:
            assert "AATESTtoken" not in str(exc)


def test_server_error_500_raises():
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Server Error"
    with patch.object(client._session, "post", return_value=resp):
        try:
            client.delete_webhook()
            pytest.fail("Expected TelegramAPIError")
        except TelegramAPIError as exc:
            assert "AATESTtoken" not in str(exc)
            assert "500" in str(exc)


def test_rate_limit_error_code():
    client = _make_client()
    resp = _api_error_response(429, "Too Many Requests: retry after 30")
    with patch.object(client._session, "post", return_value=resp):
        with pytest.raises(TelegramAPIError) as exc_info:
            client.send_message("111", "hi")
    assert exc_info.value.error_code == 429


# ---------------------------------------------------------------------------
# get_updates
# ---------------------------------------------------------------------------


def test_get_updates_returns_list():
    client = _make_client()
    updates = [{"update_id": 1, "message": {"text": "/list"}}]
    with patch.object(client._session, "post", return_value=_ok_response(updates)):
        result = client.get_updates(offset=0, timeout=1)
    assert result == updates
