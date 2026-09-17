from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin, utc_now
from domain.enums import (
    LanguageCode,
    Role,
)

if TYPE_CHECKING:
    from database.models.backup_profile import BackupProfile


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_users_telegram_id"),
        CheckConstraint("role IN ('SUPERADMIN', 'ADMIN')", name="ck_users_role"),
        CheckConstraint("language_code IN ('en', 'fa')", name="ck_users_language_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.ADMIN)
    language_code: Mapped[LanguageCode] = mapped_column(
        Enum(
            LanguageCode,
            native_enum=False,
            values_callable=lambda values: [v.value for v in values],
        ),
        default=LanguageCode.ENGLISH,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    backup_access: Mapped[list[AdminBackupAccess]] = relationship(
        back_populates="admin",
        cascade="all, delete-orphan",
        foreign_keys="AdminBackupAccess.admin_id",
    )


class AdminBackupAccess(Base):
    __tablename__ = "admin_backup_access"

    admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    backup_profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    admin: Mapped[User] = relationship(back_populates="backup_access", foreign_keys=[admin_id])
    profile: Mapped[BackupProfile] = relationship(back_populates="admin_access")
