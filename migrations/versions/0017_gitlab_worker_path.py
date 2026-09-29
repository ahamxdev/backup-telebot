"""Store only the worker-visible GitLab backup path.

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
        batch.drop_constraint("uq_gitlab_sources_host_root", type_="unique")
        batch.drop_constraint("ck_gitlab_host_path", type_="check")
        batch.drop_column("host_backup_path")


def downgrade() -> None:
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.add_column(sa.Column("host_backup_path", sa.String(length=1024), nullable=True))
    op.execute("UPDATE gitlab_sources SET host_backup_path = worker_backup_path")
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.alter_column("host_backup_path", existing_type=sa.String(length=1024), nullable=False)
        batch.create_check_constraint("ck_gitlab_host_path", "length(host_backup_path) > 1")
        batch.create_unique_constraint("uq_gitlab_sources_host_root", ["host_backup_path"])
