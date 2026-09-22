"""add first-class GitLab backup sources

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_source_type", type_="check")
        batch.create_check_constraint(
            "ck_backup_profiles_source_type",
            "source_type IN ('DATABASE', 'FILES_DIRECTORIES', 'GITLAB')",
        )
    op.create_table(
        "gitlab_sources",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("container_name", sa.String(length=128), nullable=False),
        sa.Column("host_backup_path", sa.String(length=1024), nullable=False),
        sa.Column("worker_backup_path", sa.String(length=1024), nullable=False),
        sa.CheckConstraint("length(container_name) BETWEEN 1 AND 128", name="ck_gitlab_container"),
        sa.CheckConstraint("length(host_backup_path) > 1", name="ck_gitlab_host_path"),
        sa.CheckConstraint("length(worker_backup_path) > 1", name="ck_gitlab_worker_path"),
        sa.ForeignKeyConstraint(["profile_id"], ["backup_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint("host_backup_path", name="uq_gitlab_sources_host_root"),
        sa.UniqueConstraint("worker_backup_path", name="uq_gitlab_sources_worker_root"),
    )


def downgrade() -> None:
    op.drop_table("gitlab_sources")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_source_type", type_="check")
        batch.create_check_constraint(
            "ck_backup_profiles_source_type",
            "source_type IN ('DATABASE', 'FILES_DIRECTORIES')",
        )
