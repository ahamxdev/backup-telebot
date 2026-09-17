"""Add durable backup runs, artifacts, deliveries, and schedule cursor.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.add_column(sa.Column("next_scheduled_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_backup_profiles_next_scheduled_at", ["next_scheduled_at"])
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("backup_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("skip_reason", sa.String(256)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("workspace_path", sa.String(1024)),
        sa.Column("pending_directory", sa.String(1024)),
        sa.Column("final_filename", sa.String(512)),
        sa.Column("final_size_bytes", sa.BigInteger()),
        sa.Column("final_sha256", sa.String(64)),
        sa.Column("source_metadata", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "profile_id", "trigger", "scheduled_for", name="uq_backup_runs_schedule_slot"
        ),
    )
    op.create_index("ix_backup_runs_profile_id", "backup_runs", ["profile_id"])
    op.create_index("ix_backup_runs_status", "backup_runs", ["status"])
    op.create_index("ix_backup_runs_lease_owner", "backup_runs", ["lease_owner"])
    op.create_index("ix_backup_runs_lease_expires_at", "backup_runs", ["lease_expires_at"])
    op.create_index(
        "uq_backup_runs_active_profile",
        "backup_runs",
        ["profile_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING')"),
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING', 'DELIVERY_PENDING')"),
    )
    op.create_table(
        "backup_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("backup_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "kind", name="uq_backup_artifacts_kind"),
    )
    op.create_index("ix_backup_artifacts_run_id", "backup_artifacts", ["run_id"])
    op.create_table(
        "backup_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("backup_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("telegram_file_id", sa.String(128)),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "admin_id", name="uq_backup_deliveries_recipient"),
    )
    op.create_index("ix_backup_deliveries_run_id", "backup_deliveries", ["run_id"])
    op.create_index("ix_backup_deliveries_status", "backup_deliveries", ["status"])


def downgrade() -> None:
    op.drop_table("backup_deliveries")
    op.drop_table("backup_artifacts")
    op.drop_table("backup_runs")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_index("ix_backup_profiles_next_scheduled_at")
        batch.drop_column("next_scheduled_at")
