"""Persist the remote-server authentication method.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("remote_servers") as batch:
        batch.add_column(
            sa.Column(
                "authentication_method",
                sa.String(24),
                nullable=False,
                server_default="PRIVATE_KEY",
            )
        )
        batch.create_check_constraint(
            "ck_remote_servers_authentication_method",
            "authentication_method IN ('PRIVATE_KEY')",
        )


def downgrade() -> None:
    with op.batch_alter_table("remote_servers") as batch:
        batch.drop_constraint("ck_remote_servers_authentication_method", type_="check")
        batch.drop_column("authentication_method")
