"""Add complete backup configuration and trusted remote servers.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_source_type", type_="check")
    op.execute(
        "UPDATE backup_profiles SET source_type = CASE "
        "WHEN source_type IN ('DOCKER_DATABASE', 'SERVER_DATABASE') THEN 'DATABASE' "
        "ELSE 'FILES_DIRECTORIES' END"
    )
    with op.batch_alter_table("backup_profiles") as batch:
        batch.add_column(
            sa.Column("schedule_kind", sa.String(32), nullable=False, server_default="MANUAL")
        )
        batch.add_column(sa.Column("schedule_interval", sa.Integer()))
        batch.add_column(sa.Column("schedule_time", sa.String(5)))
        batch.add_column(sa.Column("schedule_weekday", sa.Integer()))
        batch.add_column(sa.Column("schedule_monthday", sa.Integer()))
        batch.add_column(
            sa.Column(
                "compression_format", sa.String(16), nullable=False, server_default="ZSTANDARD"
            )
        )
        batch.add_column(sa.Column("compression_preset", sa.String(16), server_default="BALANCED"))
        batch.add_column(
            sa.Column("encryption_mode", sa.String(24), nullable=False, server_default="NONE")
        )
        batch.add_column(sa.Column("age_recipients", sa.Text()))
        batch.add_column(sa.Column("encrypted_age_passphrase", sa.LargeBinary()))
        batch.create_check_constraint(
            "ck_backup_profiles_source_type", "source_type IN ('DATABASE', 'FILES_DIRECTORIES')"
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_interval",
            "schedule_interval IS NULL OR schedule_interval > 0",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_weekday",
            "schedule_weekday IS NULL OR schedule_weekday BETWEEN 0 AND 6",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_monthday",
            "schedule_monthday IS NULL OR schedule_monthday BETWEEN 1 AND 28",
        )

    op.create_table(
        "remote_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(128), nullable=False),
        sa.Column("encrypted_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_key_passphrase", sa.LargeBinary()),
        sa.Column("host_key_fingerprint", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ssh_port BETWEEN 1 AND 65535", name="ck_remote_servers_port"),
    )
    op.create_index("ix_remote_servers_enabled", "remote_servers", ["enabled"])
    op.create_index("ix_remote_servers_deleted_at", "remote_servers", ["deleted_at"])

    op.create_table(
        "database_sources",
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("backup_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("engine", sa.String(20), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(128), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("encrypted_password", sa.LargeBinary(), nullable=False),
        sa.Column("tls_mode", sa.String(20), nullable=False),
        sa.Column("tls_ca_certificate", sa.Text()),
        sa.Column("mssql_in_docker", sa.Boolean(), nullable=False),
        sa.Column("mssql_backup_path", sa.String(1024)),
        sa.Column("mssql_host_backup_path", sa.String(1024)),
        sa.Column(
            "artifact_remote_server_id",
            sa.Integer(),
            sa.ForeignKey("remote_servers.id", ondelete="RESTRICT"),
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_database_sources_port"),
        sa.CheckConstraint(
            "(engine != 'MSSQL' AND mssql_in_docker = false AND mssql_backup_path IS NULL "
            "AND mssql_host_backup_path IS NULL AND artifact_remote_server_id IS NULL) OR "
            "(engine = 'MSSQL' AND mssql_backup_path IS NOT NULL AND "
            "((mssql_in_docker = false AND mssql_host_backup_path IS NULL) OR "
            "(mssql_in_docker = true AND mssql_host_backup_path IS NOT NULL)))",
            name="ck_database_sources_mssql_paths",
        ),
    )
    op.create_table(
        "files_sources",
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("backup_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("location", sa.String(20), nullable=False),
        sa.Column(
            "remote_server_id",
            sa.Integer(),
            sa.ForeignKey("remote_servers.id", ondelete="RESTRICT"),
        ),
    )
    op.create_table(
        "backup_paths",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "files_source_id",
            sa.Integer(),
            sa.ForeignKey("files_sources.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("sqlite_snapshot_candidate", sa.Boolean(), nullable=False),
        sa.CheckConstraint("length(path) > 0", name="ck_backup_paths_not_empty"),
    )
    op.create_index("ix_backup_paths_files_source_id", "backup_paths", ["files_source_id"])
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("backup_timezone", sa.String(64), nullable=False),
        sa.Column("compression_threads", sa.Integer(), nullable=False),
        sa.Column("compression_memory_limit_mb", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_settings_singleton"),
    )
    op.execute(
        "INSERT INTO system_settings (id, backup_timezone, compression_threads, "
        "compression_memory_limit_mb, created_at, updated_at) "
        "VALUES (1, 'UTC', 1, 512, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("backup_paths")
    op.drop_table("files_sources")
    op.drop_table("database_sources")
    op.drop_table("remote_servers")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_schedule_monthday", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_weekday", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_interval", type_="check")
        batch.drop_constraint("ck_backup_profiles_source_type", type_="check")
        batch.drop_column("encrypted_age_passphrase")
        batch.drop_column("age_recipients")
        batch.drop_column("encryption_mode")
        batch.drop_column("compression_preset")
        batch.drop_column("compression_format")
        batch.drop_column("schedule_monthday")
        batch.drop_column("schedule_weekday")
        batch.drop_column("schedule_time")
        batch.drop_column("schedule_interval")
        batch.drop_column("schedule_kind")
    op.execute(
        "UPDATE backup_profiles SET source_type = CASE "
        "WHEN source_type = 'DATABASE' THEN 'SERVER_DATABASE' ELSE 'FILESYSTEM' END"
    )
    with op.batch_alter_table("backup_profiles") as batch:
        batch.create_check_constraint(
            "ck_backup_profiles_source_type",
            "source_type IN ('DOCKER_DATABASE', 'SERVER_DATABASE', 'FILESYSTEM')",
        )
