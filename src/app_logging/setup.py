"""Structured and library logging without credential or exception-message leakage."""

import logging
import re
import traceback
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import structlog
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from config.settings import Settings

SECRET_KEYS = re.compile(
    r"(token|api_hash|password|session|secret|private_key|encryption_key|passphrase)", re.IGNORECASE
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S
)
_BOT_TOKEN = re.compile(r"\b[0-9]{5,}:[A-Za-z0-9_-]{20,}\b")
_SENSITIVE_VALUES: tuple[str, ...] = ()


def safe_log_text(value: str) -> str:
    value = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    value = _BOT_TOKEN.sub("[REDACTED_TOKEN]", value)
    for secret in _SENSITIVE_VALUES:
        value = value.replace(secret, "[REDACTED]")
    return value


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if SECRET_KEYS.search(str(key)) else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return safe_log_text(value)
    if isinstance(value, (SecretStr, bytes, BaseException)):
        return "[REDACTED]"
    return value


def redact_secrets(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    for key in list(event):
        if key == "exc_info":
            continue  # consumed by the safe traceback processor below
        event[key] = "[REDACTED]" if SECRET_KEYS.search(str(key)) else _safe_value(event[key])
    return event


def _safe_traceback(exc_info: Any) -> str:
    # Exception messages, chained exceptions, locals and source lines can all
    # contain secrets. Retain exception type and frame locations for diagnosis.
    kind, _value, tb = exc_info
    frames = traceback.extract_tb(tb) if tb is not None else []
    locations = [f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in frames]
    return "\n".join([*locations, getattr(kind, "__name__", "Exception")])


def safe_exception_info(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    info = event.pop("exc_info", None)
    if info:
        if not isinstance(info, tuple):
            import sys

            info = sys.exc_info()
        event["exception"] = _safe_traceback(info)
    return event


class SafeFormatter(logging.Formatter):
    def formatException(self, ei: Any) -> str:
        return _safe_traceback(ei)

    def format(self, record: logging.LogRecord) -> str:
        return safe_log_text(super().format(record))


def configure_logging(settings: Settings) -> None:
    global _SENSITIVE_VALUES
    values = [
        value.get_secret_value()
        for value in settings.__dict__.values()
        if isinstance(value, SecretStr)
    ]
    if settings.TELEGRAM_PROXY_USERNAME:
        values.append(settings.TELEGRAM_PROXY_USERNAME)
    password = make_url(settings.DATABASE_URL).password
    if password:
        values.append(password)
    _SENSITIVE_VALUES = tuple(sorted((value for value in values if value), key=len, reverse=True))
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer()
    )
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeFormatter("%(message)s"))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            safe_exception_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.LOG_LEVEL]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
