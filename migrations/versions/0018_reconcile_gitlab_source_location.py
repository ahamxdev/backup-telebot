"""Reconcile GitLab source columns after the historical 0017 migration changed.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("gitlab_sources")}

    # Some installations applied the original 0017, which only removed
    # host_backup_path.  Later edits to that already-released migration added
    # the Local/Remote columns, so Alembic correctly considered those older
    # databases current without ever applying the edited operations.
    if "location" in columns:
        return

    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("gitlab_sources")
        if constraint["name"] is not None
    }
    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("gitlab_sources")
        if constraint["name"] is not None
    }
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.add_column(
            sa.Column("location", sa.String(length=13), nullable=False, server_default="LOCAL")
        )
        batch.add_column(sa.Column("remote_server_id", sa.Integer(), nullable=True))
        batch.alter_column(
            "worker_backup_path",
            new_column_name="backup_path",
            existing_type=sa.String(length=1024),
        )
        if "uq_gitlab_sources_worker_root" in unique_names:
            batch.drop_constraint("uq_gitlab_sources_worker_root", type_="unique")
        if "ck_gitlab_worker_path" in check_names:
            batch.drop_constraint("ck_gitlab_worker_path", type_="check")
        if "host_backup_path" in columns:
            if "uq_gitlab_sources_host_root" in unique_names:
                batch.drop_constraint("uq_gitlab_sources_host_root", type_="unique")
            if "ck_gitlab_host_path" in check_names:
                batch.drop_constraint("ck_gitlab_host_path", type_="check")
            batch.drop_column("host_backup_path")
        batch.create_check_constraint(
            "ck_gitlab_sources_location", "location IN ('LOCAL', 'REMOTE_SERVER')"
        )
        batch.create_check_constraint(
            "ck_gitlab_sources_remote_consistency",
            "(location = 'LOCAL' AND remote_server_id IS NULL) OR "
            "(location = 'REMOTE_SERVER' AND remote_server_id IS NOT NULL)",
        )
        batch.create_check_constraint("ck_gitlab_backup_path", "length(backup_path) > 1")
        batch.create_foreign_key(
            "fk_gitlab_sources_remote_server_id",
            "remote_servers",
            ["remote_server_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    # 0018 makes deployed databases match the schema already declared by the
    # current 0017.  There is therefore no schema delta to reverse.
    pass
