"""separate archive containers from compression algorithms

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.add_column(
            sa.Column("archive_format", sa.String(16), nullable=False, server_default="TAR")
        )
        batch.add_column(
            sa.Column(
                "compression_algorithm",
                sa.String(16),
                nullable=False,
                server_default="ZSTANDARD",
            )
        )
    op.execute(
        "UPDATE backup_profiles SET archive_format = CASE "
        "WHEN compression_format = 'ZIP' THEN 'ZIP' "
        "WHEN compression_format = 'SEVEN_Z' THEN 'SEVEN_Z' ELSE 'TAR' END"
    )
    op.execute(
        "UPDATE backup_profiles SET compression_algorithm = CASE compression_format "
        "WHEN 'ZIP' THEN 'NONE' WHEN 'SEVEN_Z' THEN 'NONE' ELSE compression_format END"
    )
    with op.batch_alter_table("backup_profiles") as batch:
        batch.create_check_constraint(
            "ck_backup_profiles_archive_format",
            "archive_format IN ('TAR', 'ZIP', 'SEVEN_Z')",
        )
        batch.create_check_constraint(
            "ck_backup_profiles_compression_algorithm",
            "compression_algorithm IN ('NONE', 'GZIP', 'BZIP2', 'XZ', 'ZSTANDARD', 'LZ4')",
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_profiles") as batch:
        batch.drop_constraint("ck_backup_profiles_compression_algorithm", type_="check")
        batch.drop_constraint("ck_backup_profiles_archive_format", type_="check")
        batch.drop_column("compression_algorithm")
        batch.drop_column("archive_format")
