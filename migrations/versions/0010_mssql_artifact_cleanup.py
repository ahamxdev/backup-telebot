"""Track fenced MSSQL server artifacts for safe cleanup.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mssql_artifact_cleanups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("backup_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(32), nullable=False),
        sa.Column("artifact_path", sa.String(1024), nullable=False),
        sa.Column(
            "remote_server_id",
            sa.Integer(),
            sa.ForeignKey("remote_servers.id", ondelete="RESTRICT"),
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_mssql_artifact_cleanups_operation"),
        sa.CheckConstraint(
            "length(operation_id) = 32",
            name="ck_mssql_artifact_cleanups_operation_length",
        ),
        sa.CheckConstraint(
            "(retrieved_at IS NULL AND size_bytes IS NULL AND sha256 IS NULL) OR "
            "(retrieved_at IS NOT NULL AND size_bytes > 0 AND length(sha256) = 64)",
            name="ck_mssql_artifact_cleanups_verification",
        ),
    )
    op.create_index(
        "ix_mssql_artifact_cleanups_run_id",
        "mssql_artifact_cleanups",
        ["run_id"],
    )
    op.create_index(
        "ix_mssql_artifact_cleanups_retrieved_at",
        "mssql_artifact_cleanups",
        ["retrieved_at"],
    )
    op.create_index(
        "ix_mssql_artifact_cleanups_cleaned_at",
        "mssql_artifact_cleanups",
        ["cleaned_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mssql_artifact_cleanups_cleaned_at", table_name="mssql_artifact_cleanups")
    op.drop_index("ix_mssql_artifact_cleanups_retrieved_at", table_name="mssql_artifact_cleanups")
    op.drop_index("ix_mssql_artifact_cleanups_run_id", table_name="mssql_artifact_cleanups")
    op.drop_table("mssql_artifact_cleanups")
