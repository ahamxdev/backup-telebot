from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from domain.enums import CompressionFormat, CompressionPreset


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_compression_preset(context: Any) -> CompressionPreset | None:
    selected = context.get_current_parameters().get("compression_format")
    if selected in {CompressionFormat.NONE, CompressionFormat.NONE.value}:
        return None
    return CompressionPreset.BALANCED


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
