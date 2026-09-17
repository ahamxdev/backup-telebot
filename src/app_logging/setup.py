import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from config.settings import Settings

SECRET_KEYS = re.compile(r"(token|api_hash|password|session|secret)", re.IGNORECASE)


def redact_secrets(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    for key in list(event):
        if SECRET_KEYS.search(str(key)):
            event[key] = "[REDACTED]"
    return event


def configure_logging(settings: Settings) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer()
    )
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.LOG_LEVEL]
        ),
    )
