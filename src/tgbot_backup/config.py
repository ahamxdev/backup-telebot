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
    # --- Core (required) ---
    telegram_bot_token: str
    telegram_target_chat_ids: tuple[str, ...]
    watch_dir: str

    # --- File selection ---
    file_glob: str
    stable_seconds: float
    scan_interval: float
    retry_backoff: float
    max_file_size_mb: int

    # --- Startup behaviour ---
    startup_send_existing: bool
    clear_webhook_on_start: bool

    # --- Crash recovery ---
    delete_uploading_older_than_hours: float

    # --- Concurrency guard ---
    lock_file: str

    # --- Caption ---
    caption_template: str

    # --- Network ---
    request_timeout: float
    socks_proxy: str
    telegram_api_base_url: str  # override for local Bot API server

    # --- Jobs system ---
    backup_jobs_file: str         # path to jobs.toml; empty = jobs disabled

    # --- Admin notifications ---
    admin_chat_id: str            # empty = notifications disabled
    heartbeat_interval_hours: float  # 0 = disabled
    alert_consecutive_failures: int  # 0 = disabled

    # --- Pipeline defaults (overridable per job) ---
    default_encrypt_tool: str     # "age" or "gpg"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_settings(env_file: str | None = None) -> Settings:
    """Load settings from *env_file* (if given) then from os.environ.

    Also checks that the .env file itself is not group/world-writable.
    """
    if env_file:
        _check_env_file_permissions(env_file)
        _load_dotenv(env_file)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required but not set.")

    chat_ids = _split_csv(os.environ.get("TELEGRAM_TARGET_CHAT_IDS", ""))
    if not chat_ids:
        raise ValueError("TELEGRAM_TARGET_CHAT_IDS is required but not set.")

    for cid in chat_ids:
        if not re.match(r"^-?\d+$", cid):
            raise ValueError(
                f"Invalid chat_id {cid!r} in TELEGRAM_TARGET_CHAT_IDS (must be numeric)."
            )

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
        telegram_api_base_url=os.environ.get(
            "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
        ).rstrip("/"),
        backup_jobs_file=os.environ.get("BACKUP_JOBS_FILE", "").strip(),
        admin_chat_id=os.environ.get("ADMIN_CHAT_ID", "").strip(),
        heartbeat_interval_hours=_parse_float(os.environ.get("HEARTBEAT_INTERVAL_HOURS"), 0.0),
        alert_consecutive_failures=_parse_int(os.environ.get("ALERT_CONSECUTIVE_FAILURES"), 3),
        default_encrypt_tool=os.environ.get("ENCRYPT_TOOL", "age").strip().lower(),
    )


def _check_env_file_permissions(path: str) -> None:
    """Warn (not fatal) if the .env file is group/world-readable.

    The .env file contains the bot token, so it should be readable only by
    the service user. We warn rather than abort to avoid breaking existing setups.
    """
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return
    if mode & 0o044:  # group-read or world-read
        import logging as _logging
        _logging.getLogger(__name__).warning(
            ".env file %r has loose permissions (mode %s). "
            "Consider 'chmod 600 %s' to protect the bot token.",
            path, oct(mode), path,
        )
