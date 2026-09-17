"""Enforce closed Phase 1 enum sets in SQLite.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.create_check_constraint("ck_users_role", "role IN ('SUPERADMIN', 'USER')")
    with op.batch_alter_table("backup_profiles") as batch:
        batch.create_check_constraint(
            "ck_backup_profiles_source_type",
            "source_type IN ('DOCKER_DATABASE', 'SERVER_DATABASE', 'FILESYSTEM')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_status", "status IN ('DRAFT', 'ARCHIVED')"
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_status", type_="check")
        batch.drop_constraint("ck_backup_profiles_source_type", type_="check")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
