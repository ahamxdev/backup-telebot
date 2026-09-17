"""Align SQLite enum storage and enforce configuration invariants.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_kind",
            "schedule_kind IN ('MANUAL', 'EVERY_N_MINUTES', 'EVERY_N_HOURS', "
            "'EVERY_N_DAYS', 'DAILY', 'WEEKLY', 'MONTHLY')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_interval_limits",
            "(schedule_kind != 'EVERY_N_MINUTES' OR schedule_interval <= 10080) AND "
            "(schedule_kind != 'EVERY_N_HOURS' OR schedule_interval <= 8760) AND "
            "(schedule_kind != 'EVERY_N_DAYS' OR schedule_interval <= 3650)",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_time",
            "schedule_time IS NULL OR (length(schedule_time) = 5 AND "
            "substr(schedule_time, 1, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 2, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 3, 1) = ':' AND "
            "substr(schedule_time, 4, 1) BETWEEN '0' AND '5' AND "
            "substr(schedule_time, 5, 1) BETWEEN '0' AND '9' AND "
            "substr(schedule_time, 1, 2) BETWEEN '00' AND '23')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_shape",
            "(schedule_kind = 'MANUAL' AND schedule_interval IS NULL AND "
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
        )
        batch.create_check_constraint(
            "ck_backup_profiles_compression_format",
            "compression_format IN ('NONE', 'ZSTANDARD', 'GZIP', 'XZ', 'BZIP2', 'ZIP', 'SEVEN_Z')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_compression_preset",
            "compression_preset IS NULL OR compression_preset IN "
            "('FAST', 'BALANCED', 'HIGH', 'MAXIMUM')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_compression_shape",
            "(compression_format = 'NONE' AND compression_preset IS NULL) OR "
            "(compression_format != 'NONE' AND compression_preset IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_encryption_mode",
            "encryption_mode IN ('NONE', 'AGE_PUBLIC_KEY', 'AGE_PASSPHRASE')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_encryption_shape",
            "(encryption_mode = 'NONE' AND age_recipients IS NULL AND "
            "encrypted_age_passphrase IS NULL) OR "
            "(encryption_mode = 'AGE_PUBLIC_KEY' AND age_recipients IS NOT NULL AND "
            "length(age_recipients) > 0 AND "
            "encrypted_age_passphrase IS NULL) OR "
            "(encryption_mode = 'AGE_PASSPHRASE' AND age_recipients IS NULL AND "
            "encrypted_age_passphrase IS NOT NULL AND length(encrypted_age_passphrase) > 0)",
        )

    with op.batch_alter_table("database_sources") as batch:
        batch.create_check_constraint(
            "ck_database_sources_engine",
            "engine IN ('POSTGRESQL', 'MYSQL_MARIADB', 'MSSQL')",
        )
        batch.create_check_constraint(
            "ck_database_sources_tls_mode",
            "tls_mode IN ('DISABLED', 'PREFERRED', 'REQUIRED', 'VERIFY_CA', 'VERIFY_FULL')",
        )
        batch.create_check_constraint(
            "ck_database_sources_tls_ca",
            "tls_mode NOT IN ('VERIFY_CA', 'VERIFY_FULL') OR "
            "(tls_ca_certificate IS NOT NULL AND length(tls_ca_certificate) > 0)",
        )

    with op.batch_alter_table("files_sources") as batch:
        batch.create_check_constraint(
            "ck_files_sources_location", "location IN ('LOCAL', 'REMOTE_SERVER')"
        )
        batch.create_check_constraint(
            "ck_files_sources_remote_consistency",
            "(location = 'LOCAL' AND remote_server_id IS NULL) OR "
            "(location = 'REMOTE_SERVER' AND remote_server_id IS NOT NULL)",
        )

    with op.batch_alter_table("system_settings") as batch:
        batch.create_check_constraint(
            "ck_system_settings_compression_threads",
            "compression_threads BETWEEN 1 AND 64",
        )
        batch.create_check_constraint(
            "ck_system_settings_compression_memory",
            "compression_memory_limit_mb BETWEEN 16 AND 1048576",
        )

    with op.batch_alter_table("backup_runs") as batch:
        batch.create_check_constraint(
            "ck_backup_runs_trigger", "trigger IN ('MANUAL', 'SCHEDULED')"
        )
        batch.create_check_constraint(
            "ck_backup_runs_status",
            "status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING', 'SUCCEEDED', "
            "'FAILED', 'CANCELLED', 'SKIPPED')",
        )
        batch.create_check_constraint(
            "ck_backup_runs_stage",
            "stage IN ('QUEUED', 'BACKUP', 'VERIFY_SOURCE', 'COMPRESS', 'ENCRYPT', "
            "'CHECKSUM', 'FINALIZE', 'PENDING_DELIVERY', 'TELEGRAM_DELIVERY', "
            "'CLEANUP', 'COMPLETE')",
        )

    with op.batch_alter_table("backup_artifacts") as batch:
        batch.create_check_constraint("ck_backup_artifacts_kind", "kind IN ('FINAL', 'MANIFEST')")
        batch.create_check_constraint("ck_backup_artifacts_size", "size_bytes >= 0")

    with op.batch_alter_table("backup_deliveries") as batch:
        batch.create_check_constraint(
            "ck_backup_deliveries_status",
            "status IN ('PENDING', 'SUCCEEDED', 'SKIPPED', 'FAILED')",
        )
        batch.create_check_constraint("ck_backup_deliveries_attempts", "attempts >= 0")


def downgrade() -> None:
    with op.batch_alter_table("backup_deliveries") as batch:
        batch.drop_constraint("ck_backup_deliveries_attempts", type_="check")
        batch.drop_constraint("ck_backup_deliveries_status", type_="check")
    with op.batch_alter_table("backup_artifacts") as batch:
        batch.drop_constraint("ck_backup_artifacts_size", type_="check")
        batch.drop_constraint("ck_backup_artifacts_kind", type_="check")
    with op.batch_alter_table("backup_runs") as batch:
        batch.drop_constraint("ck_backup_runs_stage", type_="check")
        batch.drop_constraint("ck_backup_runs_status", type_="check")
        batch.drop_constraint("ck_backup_runs_trigger", type_="check")
    with op.batch_alter_table("system_settings") as batch:
        batch.drop_constraint("ck_system_settings_compression_memory", type_="check")
        batch.drop_constraint("ck_system_settings_compression_threads", type_="check")
    with op.batch_alter_table("files_sources") as batch:
        batch.drop_constraint("ck_files_sources_remote_consistency", type_="check")
        batch.drop_constraint("ck_files_sources_location", type_="check")
    with op.batch_alter_table("database_sources") as batch:
        batch.drop_constraint("ck_database_sources_tls_ca", type_="check")
        batch.drop_constraint("ck_database_sources_tls_mode", type_="check")
        batch.drop_constraint("ck_database_sources_engine", type_="check")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_encryption_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_encryption_mode", type_="check")
        batch.drop_constraint("ck_backup_profiles_compression_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_compression_preset", type_="check")
        batch.drop_constraint("ck_backup_profiles_compression_format", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_time", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_interval_limits", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_kind", type_="check")
