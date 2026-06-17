"""paper similarity scores

Revision ID: 20260617_0002
Revises: 20260617_0001
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260617_0002"
down_revision: str | None = "20260617_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_similarities",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("paper_id_a", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("paper_id_b", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("cosine_similarity", sa.Float(), nullable=False),
        sa.Column("algorithm", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=True),
        sa.Column("embedding_hash", sa.String(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_id_a", "paper_id_b", name="uq_paper_similarity_pair"),
    )
    op.create_index("ix_paper_similarities_paper_id_a", "paper_similarities", ["paper_id_a"])
    op.create_index("ix_paper_similarities_paper_id_b", "paper_similarities", ["paper_id_b"])
    op.create_index("ix_paper_similarities_score", "paper_similarities", ["score"])
    op.create_index("ix_paper_similarities_algorithm", "paper_similarities", ["algorithm"])
    op.create_index(
        "ix_paper_similarities_embedding_model",
        "paper_similarities",
        ["embedding_model"],
    )
    op.create_index(
        "ix_paper_similarities_embedding_hash",
        "paper_similarities",
        ["embedding_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_similarities_embedding_hash", table_name="paper_similarities")
    op.drop_index("ix_paper_similarities_embedding_model", table_name="paper_similarities")
    op.drop_index("ix_paper_similarities_algorithm", table_name="paper_similarities")
    op.drop_index("ix_paper_similarities_score", table_name="paper_similarities")
    op.drop_index("ix_paper_similarities_paper_id_b", table_name="paper_similarities")
    op.drop_index("ix_paper_similarities_paper_id_a", table_name="paper_similarities")
    op.drop_table("paper_similarities")
