"""Add durable multipart delivery progress.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_deliveries") as batch:
        batch.add_column(
            sa.Column("part_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("next_part", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("ck_backup_deliveries_part_attempts", "part_attempts >= 0")
        batch.create_check_constraint("ck_backup_deliveries_next_part", "next_part >= 1")


def downgrade() -> None:
    with op.batch_alter_table("backup_deliveries") as batch:
        batch.drop_constraint("ck_backup_deliveries_next_part", type_="check")
        batch.drop_constraint("ck_backup_deliveries_part_attempts", type_="check")
        batch.drop_column("next_part")
        batch.drop_column("part_attempts")
