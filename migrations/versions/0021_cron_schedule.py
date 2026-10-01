"""Add five-field cron backup schedules.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.add_column(sa.Column("schedule_cron", sa.String(length=200), nullable=True))
        batch.drop_constraint("ck_backup_profiles_schedule_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_kind", type_="check")
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_kind",
            "schedule_kind IN ('MANUAL', 'ONCE', 'EVERY_N_MINUTES', 'EVERY_N_HOURS', "
            "'EVERY_N_DAYS', 'DAILY', 'WEEKLY', 'MONTHLY', 'CRON')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_cron",
            "schedule_cron IS NULL OR length(schedule_cron) BETWEEN 1 AND 200",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_shape",
            "(schedule_kind IN ('MANUAL', 'ONCE') AND schedule_interval IS NULL AND "
            "schedule_time IS NULL AND schedule_weekday IS NULL AND schedule_monthday IS NULL AND "
            "schedule_cron IS NULL) OR "
            "(schedule_kind IN ('EVERY_N_MINUTES', 'EVERY_N_HOURS', 'EVERY_N_DAYS') AND "
            "schedule_interval IS NOT NULL AND schedule_time IS NULL AND "
            "schedule_weekday IS NULL AND schedule_monthday IS NULL AND schedule_cron IS NULL) OR "
            "(schedule_kind = 'DAILY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NULL AND "
            "schedule_monthday IS NULL AND schedule_cron IS NULL) OR "
            "(schedule_kind = 'WEEKLY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NOT NULL AND "
            "schedule_monthday IS NULL AND schedule_cron IS NULL) OR "
            "(schedule_kind = 'MONTHLY' AND schedule_interval IS NULL AND "
            "schedule_time IS NOT NULL AND schedule_weekday IS NULL AND "
            "schedule_monthday IS NOT NULL AND schedule_cron IS NULL) OR "
            "(schedule_kind = 'CRON' AND schedule_interval IS NULL AND schedule_time IS NULL AND "
            "schedule_weekday IS NULL AND schedule_monthday IS NULL AND schedule_cron IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE backup_profiles SET schedule_kind = 'MANUAL', schedule_cron = NULL "
        "WHERE schedule_kind = 'CRON'"
    )
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_schedule_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_cron", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_kind", type_="check")
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_kind",
            "schedule_kind IN ('MANUAL', 'ONCE', 'EVERY_N_MINUTES', 'EVERY_N_HOURS', "
            "'EVERY_N_DAYS', 'DAILY', 'WEEKLY', 'MONTHLY')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_shape",
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
        )
        batch.drop_column("schedule_cron")
