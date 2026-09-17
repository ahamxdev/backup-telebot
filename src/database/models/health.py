from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin


class SystemSettings(TimestampMixin, Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_system_settings_singleton"),
        CheckConstraint(
            "compression_threads BETWEEN 1 AND 64",
            name="ck_system_settings_compression_threads",
        ),
        CheckConstraint(
            "compression_memory_limit_mb BETWEEN 16 AND 1048576",
            name="ck_system_settings_compression_memory",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    backup_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    compression_threads: Mapped[int] = mapped_column(Integer, default=1)
    compression_memory_limit_mb: Mapped[int] = mapped_column(Integer, default=512)


class RuntimeHeartbeat(Base):
    __tablename__ = "runtime_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "component IN ('worker', 'scheduler', 'telegram_bot', 'telegram_worker')",
            name="ck_runtime_heartbeats_component",
        ),
    )

    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
