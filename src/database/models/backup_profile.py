from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin, default_compression_preset
from domain.enums import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionFormat,
    CompressionPreset,
    DatabaseEngine,
    EncryptionMode,
    ProfileStatus,
    ScheduleKind,
    SourceLocation,
    SourceType,
    TLSMode,
)

if TYPE_CHECKING:
    from database.models.backup_run import BackupRun
    from database.models.remote_server import RemoteServer
    from database.models.user import AdminBackupAccess


class BackupProfile(TimestampMixin, Base):
    __tablename__ = "backup_profiles"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('DATABASE', 'FILES_DIRECTORIES', 'GITLAB')",
            name="ck_backup_profiles_source_type",
        ),
        CheckConstraint("status IN ('DRAFT', 'ARCHIVED')", name="ck_backup_profiles_status"),
        CheckConstraint(
            "schedule_interval IS NULL OR schedule_interval > 0",
            name="ck_backup_profiles_schedule_interval",
        ),
        CheckConstraint(
            "schedule_weekday IS NULL OR schedule_weekday BETWEEN 0 AND 6",
            name="ck_backup_profiles_schedule_weekday",
        ),
        CheckConstraint(
            "schedule_monthday IS NULL OR schedule_monthday BETWEEN 1 AND 28",
            name="ck_backup_profiles_schedule_monthday",
        ),
        CheckConstraint(
            "schedule_kind IN ('MANUAL', 'ONCE', 'EVERY_N_MINUTES', 'EVERY_N_HOURS', "
            "'EVERY_N_DAYS', 'DAILY', 'WEEKLY', 'MONTHLY')",
            name="ck_backup_profiles_schedule_kind",
        ),
        CheckConstraint(
            "(schedule_kind != 'EVERY_N_MINUTES' OR schedule_interval <= 10080) AND "
            "(schedule_kind != 'EVERY_N_HOURS' OR schedule_interval <= 8760) AND "
            "(schedule_kind != 'EVERY_N_DAYS' OR schedule_interval <= 3650)",
            name="ck_backup_profiles_schedule_interval_limits",
        ),
        CheckConstraint(
            "schedule_time IS NULL OR (length(schedule_time) = 5 AND "
            "substr(schedule_time, 1, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 2, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 3, 1) = ':' AND "
            "substr(schedule_time, 4, 1) BETWEEN '0' AND '5' AND "
            "substr(schedule_time, 5, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 1, 2) BETWEEN '00' AND '23')",
            name="ck_backup_profiles_schedule_time",
        ),
        CheckConstraint(
            "(schedule_kind IN ('MANUAL', 'ONCE') AND schedule_interval IS NULL AND "
            "schedule_time IS NULL AND schedule_weekday IS NULL AND schedule_monthday IS NULL) OR "
            "(schedule_kind IN ('EVERY_N_MINUTES', 'EVERY_N_HOURS', 'EVERY_N_DAYS') AND "
            "schedule_interval IS NOT NULL AND schedule_time IS NULL AND "
            "schedule_weekday IS NULL AND schedule_monthday IS NULL) OR "
            "(schedule_kind = 'DAILY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NULL AND "
            "schedule_monthday IS NULL) OR "
            "(schedule_kind = 'WEEKLY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NOT NULL AND "
            "schedule_monthday IS NULL) OR "
            "(schedule_kind = 'MONTHLY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NULL AND "
            "schedule_monthday IS NOT NULL)",
            name="ck_backup_profiles_schedule_shape",
        ),
        CheckConstraint(
            "compression_format IN ('NONE', 'ZSTANDARD', 'GZIP', 'XZ', 'BZIP2', 'ZIP', 'SEVEN_Z')",
            name="ck_backup_profiles_compression_format",
        ),
        CheckConstraint(
            "archive_format IN ('TAR', 'ZIP', 'SEVEN_Z')",
            name="ck_backup_profiles_archive_format",
        ),
        CheckConstraint(
            "compression_algorithm IN ('NONE', 'GZIP', 'BZIP2', 'XZ', 'ZSTANDARD', 'LZ4')",
            name="ck_backup_profiles_compression_algorithm",
        ),
        CheckConstraint(
            "compression_preset IS NULL OR compression_preset IN "
            "('FAST', 'BALANCED', 'HIGH', 'MAXIMUM')",
            name="ck_backup_profiles_compression_preset",
        ),
        CheckConstraint(
            "(compression_format = 'NONE' AND compression_preset IS NULL) OR "
            "(compression_format != 'NONE' AND compression_preset IS NOT NULL)",
            name="ck_backup_profiles_compression_shape",
        ),
        CheckConstraint(
            "encryption_mode IN ('NONE', 'AGE_PUBLIC_KEY', 'AGE_PASSPHRASE')",
            name="ck_backup_profiles_encryption_mode",
        ),
        CheckConstraint(
            "(encryption_mode = 'NONE' AND age_recipients IS NULL AND "
            "encrypted_age_passphrase IS NULL) OR "
            "(encryption_mode = 'AGE_PUBLIC_KEY' AND age_recipients IS NOT NULL AND "
            "length(age_recipients) > 0 AND "
            "encrypted_age_passphrase IS NULL) OR "
            "(encryption_mode = 'AGE_PASSPHRASE' AND age_recipients IS NULL AND "
            "encrypted_age_passphrase IS NOT NULL AND length(encrypted_age_passphrase) > 0)",
            name="ck_backup_profiles_encryption_shape",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid_lib.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, native_enum=False))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[ProfileStatus] = mapped_column(
        Enum(ProfileStatus, native_enum=False), default=ProfileStatus.DRAFT
    )
    schedule_kind: Mapped[ScheduleKind] = mapped_column(
        Enum(ScheduleKind, native_enum=False), default=ScheduleKind.MANUAL
    )
    schedule_interval: Mapped[int | None] = mapped_column(Integer)
    schedule_time: Mapped[str | None] = mapped_column(String(5))
    schedule_weekday: Mapped[int | None] = mapped_column(Integer)
    schedule_monthday: Mapped[int | None] = mapped_column(Integer)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    compression_format: Mapped[CompressionFormat] = mapped_column(
        Enum(CompressionFormat, native_enum=False), default=CompressionFormat.ZSTANDARD
    )
    archive_format: Mapped[ArchiveFormat] = mapped_column(
        Enum(ArchiveFormat, native_enum=False), default=ArchiveFormat.TAR
    )
    compression_algorithm: Mapped[CompressionAlgorithm] = mapped_column(
        Enum(CompressionAlgorithm, native_enum=False), default=CompressionAlgorithm.ZSTANDARD
    )
    compression_preset: Mapped[CompressionPreset | None] = mapped_column(
        Enum(CompressionPreset, native_enum=False), default=default_compression_preset
    )
    encryption_mode: Mapped[EncryptionMode] = mapped_column(
        Enum(EncryptionMode, native_enum=False), default=EncryptionMode.NONE
    )
    age_recipients: Mapped[str | None] = mapped_column(Text)
    encrypted_age_passphrase: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    admin_access: Mapped[list[AdminBackupAccess]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    database_source: Mapped[DatabaseSource | None] = relationship(
        back_populates="profile", cascade="all, delete-orphan", uselist=False
    )
    files_source: Mapped[FilesSource | None] = relationship(
        back_populates="profile", cascade="all, delete-orphan", uselist=False
    )
    gitlab_source: Mapped[GitLabSource | None] = relationship(
        back_populates="profile", cascade="all, delete-orphan", uselist=False
    )
    runs: Mapped[list[BackupRun]] = relationship(back_populates="profile")


class DatabaseSource(Base):
    __tablename__ = "database_sources"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_database_sources_port"),
        CheckConstraint(
            "(engine != 'MSSQL' AND mssql_in_docker = false AND "
            "mssql_backup_path IS NULL AND mssql_host_backup_path IS NULL AND "
            "artifact_remote_server_id IS NULL) OR "
            "(engine = 'MSSQL' AND mssql_backup_path IS NOT NULL AND "
            "((mssql_in_docker = false AND mssql_host_backup_path IS NULL) OR "
            "(mssql_in_docker = true AND mssql_host_backup_path IS NOT NULL)))",
            name="ck_database_sources_mssql_paths",
        ),
        CheckConstraint(
            "engine IN ('POSTGRESQL', 'MYSQL_MARIADB', 'MSSQL')",
            name="ck_database_sources_engine",
        ),
        CheckConstraint(
            "tls_mode IN ('DISABLED', 'PREFERRED', 'REQUIRED', 'VERIFY_CA', 'VERIFY_FULL')",
            name="ck_database_sources_tls_mode",
        ),
        CheckConstraint(
            "tls_mode NOT IN ('VERIFY_CA', 'VERIFY_FULL') OR "
            "(tls_ca_certificate IS NOT NULL AND length(tls_ca_certificate) > 0)",
            name="ck_database_sources_tls_ca",
        ),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    engine: Mapped[DatabaseEngine] = mapped_column(Enum(DatabaseEngine, native_enum=False))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128))
    encrypted_password: Mapped[bytes] = mapped_column(LargeBinary)
    tls_mode: Mapped[TLSMode] = mapped_column(Enum(TLSMode, native_enum=False))
    tls_ca_certificate: Mapped[str | None] = mapped_column(Text)
    mssql_in_docker: Mapped[bool] = mapped_column(Boolean, default=False)
    mssql_backup_path: Mapped[str | None] = mapped_column(String(1024))
    mssql_host_backup_path: Mapped[str | None] = mapped_column(String(1024))
    artifact_remote_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_servers.id", ondelete="RESTRICT")
    )
    profile: Mapped[BackupProfile] = relationship(back_populates="database_source")
    artifact_remote_server: Mapped[RemoteServer | None] = relationship()


class FilesSource(Base):
    __tablename__ = "files_sources"
    __table_args__ = (
        CheckConstraint(
            "location IN ('LOCAL', 'REMOTE_SERVER')",
            name="ck_files_sources_location",
        ),
        CheckConstraint(
            "(location = 'LOCAL' AND remote_server_id IS NULL) OR "
            "(location = 'REMOTE_SERVER' AND remote_server_id IS NOT NULL)",
            name="ck_files_sources_remote_consistency",
        ),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    location: Mapped[SourceLocation] = mapped_column(Enum(SourceLocation, native_enum=False))
    remote_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_servers.id", ondelete="RESTRICT")
    )
    profile: Mapped[BackupProfile] = relationship(back_populates="files_source")
    remote_server: Mapped[RemoteServer | None] = relationship()
    paths: Mapped[list[BackupPath]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class GitLabSource(Base):
    __tablename__ = "gitlab_sources"
    __table_args__ = (
        CheckConstraint(
            "location IN ('LOCAL', 'REMOTE_SERVER')",
            name="ck_gitlab_sources_location",
        ),
        CheckConstraint(
            "(location = 'LOCAL' AND remote_server_id IS NULL) OR "
            "(location = 'REMOTE_SERVER' AND remote_server_id IS NOT NULL)",
            name="ck_gitlab_sources_remote_consistency",
        ),
        CheckConstraint("length(container_name) BETWEEN 1 AND 128", name="ck_gitlab_container"),
        CheckConstraint("length(backup_path) > 1", name="ck_gitlab_backup_path"),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    location: Mapped[SourceLocation] = mapped_column(Enum(SourceLocation, native_enum=False))
    remote_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_servers.id", ondelete="RESTRICT")
    )
    container_name: Mapped[str] = mapped_column(String(128))
    backup_path: Mapped[str] = mapped_column(String(1024))
    profile: Mapped[BackupProfile] = relationship(back_populates="gitlab_source")
    remote_server: Mapped[RemoteServer | None] = relationship()


class BackupPath(Base):
    __tablename__ = "backup_paths"
    __table_args__ = (CheckConstraint("length(path) > 0", name="ck_backup_paths_not_empty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    files_source_id: Mapped[int] = mapped_column(
        ForeignKey("files_sources.profile_id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1024))
    sqlite_snapshot_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[FilesSource] = relationship(back_populates="paths")
