"""Add durable runtime component heartbeats.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_heartbeats",
        sa.Column("component", sa.String(32), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "component IN ('worker', 'scheduler', 'telegram_bot', 'telegram_worker')",
            name="ck_runtime_heartbeats_component",
        ),
    )
    op.create_index("ix_runtime_heartbeats_heartbeat_at", "runtime_heartbeats", ["heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_heartbeats_heartbeat_at", table_name="runtime_heartbeats")
    op.drop_table("runtime_heartbeats")
