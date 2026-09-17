from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin
from domain.enums import (
    RunStage,
    RunStatus,
    RunTrigger,
)

if TYPE_CHECKING:
    from database.models.artifact import BackupArtifact
    from database.models.backup_profile import BackupProfile
    from database.models.delivery import BackupDelivery
    from database.models.remote_server import RemoteServer


class BackupRun(TimestampMixin, Base):
    __tablename__ = "backup_runs"
    __table_args__ = (
        Index(
            "uq_backup_runs_active_profile",
            "profile_id",
            unique=True,
            sqlite_where=text("status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING')"),
            postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING')"),
        ),
        UniqueConstraint(
            "profile_id", "trigger", "scheduled_for", name="uq_backup_runs_schedule_slot"
        ),
        CheckConstraint(
            "trigger IN ('MANUAL', 'SCHEDULED')",
            name="ck_backup_runs_trigger",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING', 'SUCCEEDED', "
            "'FAILED', 'CANCELLED', 'SKIPPED')",
            name="ck_backup_runs_status",
        ),
        CheckConstraint(
            "stage IN ('QUEUED', 'BACKUP', 'VERIFY_SOURCE', 'COMPRESS', 'ENCRYPT', "
            "'CHECKSUM', 'FINALIZE', 'PENDING_DELIVERY', 'TELEGRAM_DELIVERY', "
            "'CLEANUP', 'COMPLETE')",
            name="ck_backup_runs_stage",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid_lib.uuid4())
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id", ondelete="RESTRICT"), index=True
    )
    trigger: Mapped[RunTrigger] = mapped_column(Enum(RunTrigger, native_enum=False))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, native_enum=False), index=True)
    stage: Mapped[RunStage] = mapped_column(Enum(RunStage, native_enum=False))
    skip_reason: Mapped[str | None] = mapped_column(String(256))
    lease_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workspace_path: Mapped[str | None] = mapped_column(String(1024))
    pending_directory: Mapped[str | None] = mapped_column(String(1024))
    failed_artifact_cleanup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    failed_artifact_cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_filename: Mapped[str | None] = mapped_column(String(512))
    final_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    final_sha256: Mapped[str | None] = mapped_column(String(64))
    source_metadata: Mapped[str | None] = mapped_column(Text)
    configuration_snapshot: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[BackupProfile] = relationship(back_populates="runs")
    artifacts: Mapped[list[BackupArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list[BackupDelivery]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    mssql_artifact_cleanups: Mapped[list[MSSQLArtifactCleanup]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MSSQLArtifactCleanup(TimestampMixin, Base):
    __tablename__ = "mssql_artifact_cleanups"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_mssql_artifact_cleanups_operation"),
        CheckConstraint(
            "length(operation_id) = 32",
            name="ck_mssql_artifact_cleanups_operation_length",
        ),
        CheckConstraint(
            "(retrieved_at IS NULL AND size_bytes IS NULL AND sha256 IS NULL) OR "
            "(retrieved_at IS NOT NULL AND size_bytes > 0 AND length(sha256) = 64)",
            name="ck_mssql_artifact_cleanups_verification",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), index=True
    )
    operation_id: Mapped[str] = mapped_column(String(32))
    artifact_path: Mapped[str] = mapped_column(String(1024))
    remote_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_servers.id", ondelete="RESTRICT")
    )
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(64))
    run: Mapped[BackupRun] = relationship(back_populates="mssql_artifact_cleanups")
    remote_server: Mapped[RemoteServer | None] = relationship()
