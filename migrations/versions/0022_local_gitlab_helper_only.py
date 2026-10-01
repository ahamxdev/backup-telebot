"""Remove the obsolete native path from Local GitLab profiles.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.drop_constraint("ck_gitlab_backup_path", type_="check")
        batch.alter_column("backup_path", existing_type=sa.String(length=1024), nullable=True)
    op.execute("UPDATE gitlab_sources SET backup_path = NULL WHERE location = 'LOCAL'")
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.create_check_constraint(
            "ck_gitlab_backup_path",
            "(location = 'LOCAL' AND backup_path IS NULL) OR "
            "(location = 'REMOTE_SERVER' AND length(backup_path) > 1)",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE gitlab_sources SET backup_path = '/gitlab-backups' "
        "WHERE location = 'LOCAL' AND backup_path IS NULL"
    )
    with op.batch_alter_table("gitlab_sources") as batch:
        batch.drop_constraint("ck_gitlab_backup_path", type_="check")
        batch.alter_column("backup_path", existing_type=sa.String(length=1024), nullable=False)
        batch.create_check_constraint("ck_gitlab_backup_path", "length(backup_path) > 1")
