"""Persist the acquired backup size before transforms.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_runs") as batch:
        batch.add_column(sa.Column("original_size_bytes", sa.BigInteger(), nullable=True))
        batch.create_check_constraint("ck_backup_runs_original_size", "original_size_bytes >= 0")


def downgrade() -> None:
    with op.batch_alter_table("backup_runs") as batch:
        batch.drop_constraint("ck_backup_runs_original_size", type_="check")
        batch.drop_column("original_size_bytes")
