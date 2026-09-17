from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin
from domain.enums import (
    ArtifactKind,
)

if TYPE_CHECKING:
    from database.models.backup_run import BackupRun


class BackupArtifact(TimestampMixin, Base):
    __tablename__ = "backup_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", name="uq_backup_artifacts_kind"),
        CheckConstraint("kind IN ('FINAL', 'MANIFEST')", name="ck_backup_artifacts_kind"),
        CheckConstraint("size_bytes >= 0", name="ck_backup_artifacts_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(Enum(ArtifactKind, native_enum=False))
    relative_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    run: Mapped[BackupRun] = relationship(back_populates="artifacts")
