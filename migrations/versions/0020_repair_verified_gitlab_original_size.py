"""Repair original size from verified native GitLab acquisition metadata.

Revision ID: 0020
Revises: 0019
"""

import json
import string
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HEX = frozenset(string.hexdigits)


def _verified_gitlab_native_size(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    size = metadata.get("native_size")
    checksum = metadata.get("native_sha256")
    filename = metadata.get("native_filename")
    if (
        metadata.get("kind") != "gitlab"
        or metadata.get("verified") is not True
        or type(size) is not int
        or size < 0
        or not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in _HEX for character in checksum)
        or not isinstance(filename, str)
        or not filename.endswith("_gitlab_backup.tar")
    ):
        return None
    return size


def upgrade() -> None:
    connection = op.get_bind()
    runs = sa.table(
        "backup_runs",
        sa.column("id", sa.Integer()),
        sa.column("source_metadata", sa.Text()),
        sa.column("original_size_bytes", sa.BigInteger()),
    )
    rows = list(
        connection.execute(
            sa.select(runs.c.id, runs.c.source_metadata).where(
                runs.c.original_size_bytes.is_(None),
                runs.c.source_metadata.is_not(None),
            )
        )
    )
    for run_id, source_metadata in rows:
        size = _verified_gitlab_native_size(source_metadata)
        if size is not None:
            connection.execute(
                runs.update()
                .where(runs.c.id == run_id, runs.c.original_size_bytes.is_(None))
                .values(original_size_bytes=size)
            )


def downgrade() -> None:
    # This is an intentionally forward-only data repair. A downgrade must not discard
    # a verified size after it has been restored.
    pass
