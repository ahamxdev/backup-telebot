"""Add Local/Remote GitLab source locations.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        batch.drop_constraint("uq_gitlab_sources_host_root", type_="unique")
        batch.drop_constraint("uq_gitlab_sources_worker_root", type_="unique")
        batch.drop_constraint("ck_gitlab_host_path", type_="check")
        batch.drop_constraint("ck_gitlab_worker_path", type_="check")
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
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.drop_constraint("fk_gitlab_sources_remote_server_id", type_="foreignkey")
        batch.drop_constraint("ck_gitlab_sources_remote_consistency", type_="check")
        batch.drop_constraint("ck_gitlab_sources_location", type_="check")
        batch.drop_constraint("ck_gitlab_backup_path", type_="check")
        batch.alter_column(
            "backup_path",
            new_column_name="worker_backup_path",
            existing_type=sa.String(length=1024),
        )
        batch.add_column(sa.Column("host_backup_path", sa.String(length=1024), nullable=True))
        batch.drop_column("remote_server_id")
        batch.drop_column("location")
    op.execute("UPDATE gitlab_sources SET host_backup_path = worker_backup_path")
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.alter_column("host_backup_path", existing_type=sa.String(length=1024), nullable=False)
        batch.create_check_constraint("ck_gitlab_host_path", "length(host_backup_path) > 1")
        batch.create_check_constraint("ck_gitlab_worker_path", "length(worker_backup_path) > 1")
        batch.create_unique_constraint("uq_gitlab_sources_host_root", ["host_backup_path"])
        batch.create_unique_constraint("uq_gitlab_sources_worker_root", ["worker_backup_path"])
