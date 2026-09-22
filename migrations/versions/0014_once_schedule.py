"""Add the one-time backup schedule.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_schedule_shape", type_="check")
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


def downgrade() -> None:
    op.execute("UPDATE backup_profiles SET schedule_kind = 'MANUAL' WHERE schedule_kind = 'ONCE'")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_schedule_shape", type_="check")
        batch.drop_constraint("ck_backup_profiles_schedule_kind", type_="check")
        batch.create_check_constraint(
            "ck_backup_profiles_schedule_kind",
            "schedule_kind IN ('MANUAL', 'EVERY_N_MINUTES', 'EVERY_N_HOURS', "
            "'EVERY_N_DAYS', 'DAILY', 'WEEKLY', 'MONTHLY')",
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
