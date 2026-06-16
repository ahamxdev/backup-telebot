"""Configuration layer — reads all settings from environment variables or an optional .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Hand-rolled .env loader (no library dependency)
# ---------------------------------------------------------------------------

def _load_dotenv(env_file: str) -> None:
    """Parse KEY=VALUE lines from *env_file* and inject into os.environ via setdefault.

    System env vars always win over .env values.
    Supports: blank lines, # comments, 'export KEY=VALUE', quoted values.
    """
    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip surrounding matching quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_bool(value: str | None, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int((value or "").strip())
    except (ValueError, AttributeError):
        return default


def _parse_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float((value or "").strip())
    except (ValueError, AttributeError):
        return default


def _split_csv(value: str | None) -> tuple[str, ...]:
    """Split on comma, semicolon, Persian comma (،), or newline; trim and drop empties."""
    if not value:
        return ()
    parts = re.split(r"[,;،\n]", value)
    return tuple(p.strip() for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Default caption template
# ---------------------------------------------------------------------------

_DEFAULT_CAPTION = (
    "File: {filename}\n"
    "Size: {size_human}\n"
    "Server: {hostname}\n"
    "Public IP: {public_ip}"
)

# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_target_chat_ids: tuple[str, ...]
    watch_dir: str
    file_glob: str
    stable_seconds: float
    scan_interval: float
    retry_backoff: float
    max_file_size_mb: int
    startup_send_existing: bool
    clear_webhook_on_start: bool
    delete_uploading_older_than_hours: float
    lock_file: str
    caption_template: str
    request_timeout: float
    socks_proxy: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_settings(env_file: str | None = None) -> Settings:
    """Load settings from *env_file* (if given) then from os.environ."""
    if env_file:
        _load_dotenv(env_file)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required but not set.")

    chat_ids = _split_csv(os.environ.get("TELEGRAM_TARGET_CHAT_IDS", ""))
    if not chat_ids:
        raise ValueError("TELEGRAM_TARGET_CHAT_IDS is required but not set.")

    watch_dir = os.environ.get("WATCH_DIR", "").strip()
    if not watch_dir:
        raise ValueError("WATCH_DIR is required but not set.")

    return Settings(
        telegram_bot_token=token,
        telegram_target_chat_ids=chat_ids,
        watch_dir=watch_dir,
        file_glob=os.environ.get("FILE_GLOB", "*"),
        stable_seconds=_parse_float(os.environ.get("STABLE_SECONDS"), 20.0),
        scan_interval=_parse_float(os.environ.get("SCAN_INTERVAL"), 5.0),
        retry_backoff=_parse_float(os.environ.get("RETRY_BACKOFF"), 30.0),
        max_file_size_mb=_parse_int(os.environ.get("MAX_FILE_SIZE_MB"), 50),
        startup_send_existing=_parse_bool(os.environ.get("STARTUP_SEND_EXISTING"), False),
        clear_webhook_on_start=_parse_bool(os.environ.get("CLEAR_WEBHOOK_ON_START", "true"), True),
        delete_uploading_older_than_hours=_parse_float(
            os.environ.get("DELETE_UPLOADING_OLDER_THAN_HOURS"), 0.0
        ),
        lock_file=os.environ.get("LOCK_FILE", "/tmp/telegram-backup-bot.lock"),
        caption_template=os.environ.get("CAPTION_TEMPLATE", _DEFAULT_CAPTION),
        request_timeout=_parse_float(os.environ.get("REQUEST_TIMEOUT"), 120.0),
        socks_proxy=os.environ.get("SOCKS_PROXY", "").strip(),
    )
