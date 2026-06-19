"""local search FTS index

Revision ID: 20260619_0004
Revises: 20260619_0003
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260619_0004"
down_revision: str | None = "20260619_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS paper_search_fts USING fts5(
            chunk_id UNINDEXED,
            paper_id UNINDEXED,
            title,
            abstract,
            doi,
            venue,
            authors,
            heading,
            text,
            page_start UNINDEXED,
            page_end UNINDEXED,
            tokenize = 'unicode61'
        )
        """
    )
    op.execute(
        """
        INSERT INTO paper_search_fts (
            rowid, chunk_id, paper_id, title, abstract, doi, venue, authors,
            heading, text, page_start, page_end
        )
        SELECT
            c.id,
            c.id,
            c.paper_id,
            p.title,
            COALESCE(p.abstract, ''),
            COALESCE(p.doi, ''),
            COALESCE(p.venue, ''),
            COALESCE(CAST(p.authors_json AS TEXT), ''),
            COALESCE(c.heading, ''),
            c.text,
            c.page_start,
            c.page_end
        FROM paper_chunks c
        JOIN papers p ON p.id = c.paper_id
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_search_fts")
