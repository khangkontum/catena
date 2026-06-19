"""extraction table source_path for folder imports

Adds a nullable, indexed ``source_path`` column to ``extraction_tables``.
Folder imports (`papers add-dir`) bind a table to the resolved absolute path of
the imported directory so the same folder deterministically maps to the same
table across runs (idempotent imports). The column is null for tables created
any other way.

Revision ID: 20260619_0003
Revises: 20260617_0002
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260619_0003"
down_revision: str | None = "20260617_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_tables") as batch_op:
        batch_op.add_column(sa.Column("source_path", sa.String(), nullable=True))
    op.create_index(
        "ix_extraction_tables_source_path",
        "extraction_tables",
        ["source_path"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_tables_source_path", table_name="extraction_tables")
    with op.batch_alter_table("extraction_tables") as batch_op:
        batch_op.drop_column("source_path")
