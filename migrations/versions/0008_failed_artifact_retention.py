"""Track failed-artifact retention and cleanup.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_runs") as batch:
        batch.add_column(sa.Column("failed_artifact_cleanup_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("failed_artifact_cleaned_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_backup_runs_failed_artifact_cleanup_at",
            ["failed_artifact_cleanup_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_runs") as batch:
        batch.drop_index("ix_backup_runs_failed_artifact_cleanup_at")
        batch.drop_column("failed_artifact_cleaned_at")
        batch.drop_column("failed_artifact_cleanup_at")
