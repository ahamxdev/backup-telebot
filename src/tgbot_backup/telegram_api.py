"""Network layer — thin HTTP client over the Telegram Bot API.

One requests.Session is kept alive for connection pooling.
All traffic is routed through the configured SOCKS proxy when SOCKS_PROXY is set.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class TelegramAPIError(Exception):
    """Raised for any Telegram API failure — network, HTTP, or application-level."""

    def __init__(
        self,
        message: str,
        error_code: int | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.description = description


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_proxy_url(url: str) -> str:
    """Replace user:pass in a proxy URL with *** for safe logging."""
    return re.sub(r"(://)[^:@/]+:[^@/]+@", r"\1***:***@", url)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class TelegramBotClient:
    """Stateful HTTP client for the Telegram Bot API."""

    def __init__(self, token: str, socks_proxy: str = "", timeout: float = 120.0) -> None:
        self._token = token
        self._default_timeout = timeout
        self._session = requests.Session()

        if socks_proxy:
            # PySocks must be installed for socks5h:// support
            try:
                import socks  # noqa: F401  (imported only to verify availability)
            except ImportError:
                raise TelegramAPIError(
                    "SOCKS_PROXY is configured but PySocks is not installed. "
                    "Install it with: pip install 'requests[socks]'"
                )
            self._session.proxies = {"http": socks_proxy, "https": socks_proxy}
            logger.info("Telegram traffic routed via proxy: %s", _mask_proxy_url(socks_proxy))
        else:
            logger.info("Telegram traffic: direct connection (no proxy configured).")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _call(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """POST to *method* and return the ``result`` field, or raise TelegramAPIError."""
        url = self._url(method)
        effective_timeout = timeout if timeout is not None else self._default_timeout
        try:
            response = self._session.post(
                url, data=data, files=files, timeout=effective_timeout
            )
        except requests.exceptions.ConnectionError as exc:
            raise TelegramAPIError(f"Connection error calling {method}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise TelegramAPIError(f"Timeout calling {method} after {effective_timeout}s: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise TelegramAPIError(f"Request error calling {method}: {exc}") from exc

        if response.status_code >= 500:
            raise TelegramAPIError(
                f"Telegram server error {response.status_code} on {method}: "
                f"{response.text[:300]}"
            )
        if response.status_code >= 400:
            raise TelegramAPIError(
                f"Telegram client error {response.status_code} on {method}: "
                f"{response.text[:300]}"
            )

        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise TelegramAPIError(
                f"Non-JSON response from {method}: {response.text[:300]}"
            ) from exc

        if not body.get("ok"):
            raise TelegramAPIError(
                f"Telegram API returned ok=false on {method}: "
                f"{body.get('description', 'no description')}",
                error_code=body.get("error_code"),
                description=body.get("description"),
            )

        return body["result"]

    # ------------------------------------------------------------------
    # Public API surface
    # ------------------------------------------------------------------

    def delete_webhook(self) -> None:
        """Call deleteWebhook so long-polling is usable (no incoming webhooks processed)."""
        self._call("deleteWebhook", data={"drop_pending_updates": "false"})
        logger.info("Webhook cleared via deleteWebhook.")

    def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Upload *file_path* to *chat_id* as a document.

        The file is streamed directly from disk — never fully loaded into RAM.
        *filename* sets the name visible in Telegram (use the original backup name,
        not the .uploading working name).
        """
        original_name = filename or Path(file_path).name
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption

        with open(file_path, "rb") as fh:
            files = {"document": (original_name, fh, "application/octet-stream")}
            result = self._call("sendDocument", data=data, files=files)

        return result  # type: ignore[return-value]

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Long-poll for updates (used only by the helper script, not the main service)."""
        data: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        # Allow extra seconds for the HTTP round-trip on top of the long-poll timeout
        return self._call("getUpdates", data=data, timeout=float(timeout) + 15)  # type: ignore[return-value]
