from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin
from domain.enums import (
    DeliveryStatus,
)

if TYPE_CHECKING:
    from database.models.backup_run import BackupRun


class BackupDelivery(TimestampMixin, Base):
    __tablename__ = "backup_deliveries"
    __table_args__ = (
        UniqueConstraint("run_id", "admin_id", name="uq_backup_deliveries_recipient"),
        Index("ix_backup_deliveries_retry_due", "status", "next_attempt_at"),
        CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'SKIPPED', 'FAILED')",
            name="ck_backup_deliveries_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_backup_deliveries_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), index=True
    )
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, native_enum=False), default=DeliveryStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_file_id: Mapped[str | None] = mapped_column(String(128))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    run: Mapped[BackupRun] = relationship(back_populates="deliveries")
