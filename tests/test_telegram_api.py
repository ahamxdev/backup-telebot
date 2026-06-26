"""Tests for telegram_api.py — HTTP client and token masking."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from tgbot_backup.telegram_api import (
    TelegramAPIError,
    TelegramBotClient,
    _mask_proxy_url,
    _mask_token_in_url,
)


# ---------------------------------------------------------------------------
# Token masking
# ---------------------------------------------------------------------------

class TestMaskTokenInUrl:
    def test_masks_standard_url(self):
        url = "https://api.telegram.org/bot123:secret_token/sendMessage"
        masked = _mask_token_in_url(url)
        assert "secret_token" not in masked
        assert "123" not in masked or "***" in masked
        assert "sendMessage" in masked

    def test_custom_base_url(self):
        url = "http://localhost:8081/botABC:XYZ123/sendDocument"
        masked = _mask_token_in_url(url)
        assert "XYZ123" not in masked
        assert "sendDocument" in masked

    def test_no_token_passthrough(self):
        url = "https://example.com/api/v1/data"
        # URL without /bot<token>/ pattern — should pass through unchanged
        masked = _mask_token_in_url(url)
        assert masked == url


class TestMaskProxyUrl:
    def test_masks_credentials(self):
        url = "socks5h://user:password@proxy.example.com:1080"
        masked = _mask_proxy_url(url)
        assert "password" not in masked
        assert "user" not in masked
        assert "proxy.example.com" in masked

    def test_no_credentials_passthrough(self):
        url = "socks5h://127.0.0.1:1080"
        assert _mask_proxy_url(url) == url


# ---------------------------------------------------------------------------
# TelegramBotClient
# ---------------------------------------------------------------------------

def _make_response(ok: bool = True, result: object = {}, status_code: int = 200, **kwargs):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {"ok": ok, "result": result, **kwargs}
    mock.text = json.dumps({"ok": ok, "result": result})
    return mock


class TestTelegramBotClient:
    def _client(self):
        return TelegramBotClient(token="123:test_token_abc")

    def test_token_not_in_exception_message(self):
        import requests as _requests
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.side_effect = _requests.exceptions.ConnectionError("network down")
            with pytest.raises(TelegramAPIError) as exc_info:
                client._call("sendMessage", data={"chat_id": "1", "text": "hi"})
            assert "test_token_abc" not in str(exc_info.value)

    def test_delete_webhook_calls_correct_endpoint(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _make_response(ok=True, result=True)
            client.delete_webhook()
            args, kwargs = mock_post.call_args
            url = args[0]
            assert "deleteWebhook" in url
            assert "test_token_abc" in url  # token IS in the request URL (expected)

    def test_send_document_calls_sendDocument(self, tmp_path):
        f = tmp_path / "backup.tar.gz"
        f.write_bytes(b"fake data")
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _make_response(ok=True, result={"message_id": 1})
            result = client.send_document(
                chat_id="-100123",
                file_path=str(f),
                caption="Test",
                filename="backup.tar.gz",
            )
            args, kwargs = mock_post.call_args
            url = args[0]
            assert "sendDocument" in url

    def test_send_message_calls_sendMessage(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _make_response(ok=True, result={"message_id": 2})
            client.send_message(chat_id="-100", text="hello")
            url = mock_post.call_args[0][0]
            assert "sendMessage" in url

    def test_ok_false_raises_telegram_api_error(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            }
            mock_post.return_value = resp
            with pytest.raises(TelegramAPIError) as exc_info:
                client._call("sendMessage", data={})
            assert exc_info.value.error_code == 400
            assert "test_token_abc" not in str(exc_info.value)

    def test_429_error_code_preserved(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 30",
            }
            mock_post.return_value = resp
            with pytest.raises(TelegramAPIError) as exc_info:
                client._call("sendMessage", data={})
            assert exc_info.value.error_code == 429

    def test_server_error_500_raises_and_masks_token(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "Internal Server Error"
            mock_post.return_value = resp
            try:
                client._call("sendMessage", data={})
            except TelegramAPIError as e:
                assert "test_token_abc" not in str(e)
            else:
                pytest.fail("Expected TelegramAPIError")

    def test_custom_api_base_url(self, tmp_path):
        client = TelegramBotClient(
            token="123:tok",
            api_base_url="http://localhost:8081",
        )
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = _make_response(ok=True, result=True)
            client.delete_webhook()
            url = mock_post.call_args[0][0]
            assert url.startswith("http://localhost:8081")

    def test_non_json_response_raises(self):
        client = self._client()
        with patch.object(client._session, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.side_effect = ValueError("not json")
            resp.text = "not json"
            mock_post.return_value = resp
            with pytest.raises(TelegramAPIError, match="Non-JSON"):
                client._call("sendMessage", data={})
