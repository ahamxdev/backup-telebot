from dataclasses import dataclass
from html import escape
from typing import Any


class TelegramHtml(str):
    """HTML that is already safe to pass to Telegram."""


def escaped(value: Any) -> TelegramHtml:
    return TelegramHtml(escape(str(value), quote=True))


def code(value: Any) -> TelegramHtml:
    return TelegramHtml(f"<code>{escaped(value)}</code>")


def format_bytes_iec(value: int) -> str:
    """Format an exact byte count compactly with IEC units."""
    if value < 0:
        raise ValueError("byte count cannot be negative")
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units[:-1]:
        if amount < 1024:
            return f"{int(amount)} B" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} {units[-1]}"


@dataclass(frozen=True)
class EscapedFormatValue:
    value: Any

    def __format__(self, format_spec: str) -> str:
        return escaped(format(self.value, format_spec))


def safe_format_value(value: Any) -> Any:
    if isinstance(value, TelegramHtml):
        return value
    return EscapedFormatValue(value)
