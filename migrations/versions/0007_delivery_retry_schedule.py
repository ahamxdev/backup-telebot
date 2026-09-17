"""Add durable per-recipient delivery retry scheduling.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_deliveries") as batch:
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_backup_deliveries_retry_due",
            ["status", "next_attempt_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_deliveries") as batch:
        batch.drop_index("ix_backup_deliveries_retry_due")
        batch.drop_column("next_attempt_at")
