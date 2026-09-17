"""Replace USER with ADMIN, add language, and rename access table.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove the old closed set before rewriting USER values to ADMIN.
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.add_column(
            sa.Column("language_code", sa.String(2), nullable=False, server_default="en")
        )
        batch.create_check_constraint("ck_users_language_code", "language_code IN ('en', 'fa')")
    op.execute("UPDATE users SET role = 'ADMIN' WHERE role = 'USER'")
    with op.batch_alter_table("users") as batch:
        batch.create_check_constraint("ck_users_role", "role IN ('SUPERADMIN', 'ADMIN')")
    op.rename_table("user_backup_access", "admin_backup_access")
    with op.batch_alter_table("admin_backup_access") as batch:
        batch.alter_column("user_id", new_column_name="admin_id")


def downgrade() -> None:
    with op.batch_alter_table("admin_backup_access") as batch:
        batch.alter_column("admin_id", new_column_name="user_id")
    op.rename_table("admin_backup_access", "user_backup_access")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_language_code", type_="check")
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("language_code")
    op.execute("UPDATE users SET role = 'USER' WHERE role = 'ADMIN'")
    with op.batch_alter_table("users") as batch:
        batch.create_check_constraint("ck_users_role", "role IN ('SUPERADMIN', 'USER')")
