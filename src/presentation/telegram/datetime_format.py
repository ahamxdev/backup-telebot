from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from presentation.telegram.formatting import TelegramHtml, code, escaped


def timezone_label(value: datetime, timezone: str) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    local = aware.astimezone(ZoneInfo(timezone))
    offset = local.utcoffset()
    if offset is None:
        raise ValueError("timezone offset is unavailable")
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, remainder = divmod(abs(minutes), 60)
    return f"{timezone} (UTC{sign}{hours:02d}:{remainder:02d})"


def format_user_datetime(
    value: datetime | None, timezone: str, timezone_heading: str = "Timezone"
) -> TelegramHtml:
    if value is None:
        return TelegramHtml("—")
    return TelegramHtml(
        f"{format_user_timestamp(value, timezone)}\n"
        f"{escaped(timezone_heading)}: {escaped(timezone_label(value, timezone))}"
    )


def format_user_timestamp(value: datetime | None, timezone: str) -> TelegramHtml:
    if value is None:
        return TelegramHtml("—")
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    local = aware.astimezone(ZoneInfo(timezone))
    return code(f"{local:%Y-%m-%d %H:%M:%S}")
