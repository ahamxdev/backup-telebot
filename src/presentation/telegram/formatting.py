from dataclasses import dataclass
from html import escape
from typing import Any


class TelegramHtml(str):
    """HTML that is already safe to pass to Telegram."""


def escaped(value: Any) -> TelegramHtml:
    return TelegramHtml(escape(str(value), quote=True))


def code(value: Any) -> TelegramHtml:
    return TelegramHtml(f"<code>{escaped(value)}</code>")


@dataclass(frozen=True)
class EscapedFormatValue:
    value: Any

    def __format__(self, format_spec: str) -> str:
        return escaped(format(self.value, format_spec))


def safe_format_value(value: Any) -> Any:
    if isinstance(value, TelegramHtml):
        return value
    return EscapedFormatValue(value)
