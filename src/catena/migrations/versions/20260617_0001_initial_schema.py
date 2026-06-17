"""initial schema

Revision ID: 20260617_0001
Revises:
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260617_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("stored_pdf_path", sa.String(), nullable=True),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("authors_json", sa.JSON(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(), nullable=True),
        sa.Column("publication_date", sa.String(), nullable=True),
        sa.Column("citation_count", sa.Integer(), nullable=True),
        sa.Column("abstract", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("parse_status", sa.String(), nullable=False, server_default="new"),
        sa.Column("index_status", sa.String(), nullable=False, server_default="new"),
        sa.Column("parse_error", sa.String(), nullable=True),
        sa.Column("docling_json_path", sa.String(), nullable=True),
        sa.Column("markdown_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_papers_title", "papers", ["title"])
    op.create_index("ix_papers_doi", "papers", ["doi"])
    op.create_index("ix_papers_year", "papers", ["year"])
    op.create_index("ix_papers_venue", "papers", ["venue"])
    op.create_index("ix_papers_citation_count", "papers", ["citation_count"])
    op.create_index("ix_papers_content_hash", "papers", ["content_hash"])
    op.create_index("ix_papers_parse_status", "papers", ["parse_status"])
    op.create_index("ix_papers_index_status", "papers", ["index_status"])

    op.create_table(
        "extraction_tables",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("source_filter_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_extraction_tables_name", "extraction_tables", ["name"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("normalized_name", name="uq_tag_normalized_name"),
    )
    op.create_index("ix_tags_name", "tags", ["name"])
    op.create_index("ix_tags_normalized_name", "tags", ["normalized_name"])

    op.create_table(
        "paper_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("heading", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(), nullable=True),
        sa.Column("parser_hash", sa.String(), nullable=True),
        sa.Column("embedding_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_id", "chunk_index", name="uq_chunk_paper_index"),
    )
    op.create_index("ix_paper_chunks_paper_id", "paper_chunks", ["paper_id"])
    op.create_index("ix_paper_chunks_page_start", "paper_chunks", ["page_start"])
    op.create_index("ix_paper_chunks_parser_hash", "paper_chunks", ["parser_hash"])
    op.create_index("ix_paper_chunks_embedding_hash", "paper_chunks", ["embedding_hash"])

    op.create_table(
        "paper_tags",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_id", "tag_id", name="uq_paper_tag"),
    )
    op.create_index("ix_paper_tags_paper_id", "paper_tags", ["paper_id"])
    op.create_index("ix_paper_tags_tag_id", "paper_tags", ["tag_id"])

    op.create_table(
        "table_papers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "table_id",
            sa.Integer(),
            sa.ForeignKey("extraction_tables.id"),
            nullable=False,
        ),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="included"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("table_id", "paper_id", name="uq_table_paper"),
    )
    op.create_index("ix_table_papers_table_id", "table_papers", ["table_id"])
    op.create_index("ix_table_papers_paper_id", "table_papers", ["paper_id"])
    op.create_index("ix_table_papers_status", "table_papers", ["status"])

    op.create_table(
        "extraction_columns",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "table_id",
            sa.Integer(),
            sa.ForeignKey("extraction_tables.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("output_type", sa.String(), nullable=False, server_default="text"),
        sa.Column("retrieval_query", sa.String(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("output_schema_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("table_id", "name", name="uq_column_table_name"),
    )
    op.create_index("ix_extraction_columns_table_id", "extraction_columns", ["table_id"])
    op.create_index("ix_extraction_columns_name", "extraction_columns", ["name"])

    op.create_table(
        "extraction_cells",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "table_id",
            sa.Integer(),
            sa.ForeignKey("extraction_tables.id"),
            nullable=False,
        ),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column(
            "column_id",
            sa.Integer(),
            sa.ForeignKey("extraction_columns.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("answer_text", sa.String(), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "table_id",
            "paper_id",
            "column_id",
            name="uq_cell_table_paper_column",
        ),
    )
    op.create_index("ix_extraction_cells_table_id", "extraction_cells", ["table_id"])
    op.create_index("ix_extraction_cells_paper_id", "extraction_cells", ["paper_id"])
    op.create_index("ix_extraction_cells_column_id", "extraction_cells", ["column_id"])
    op.create_index("ix_extraction_cells_status", "extraction_cells", ["status"])


def downgrade() -> None:
    op.drop_index("ix_extraction_cells_status", table_name="extraction_cells")
    op.drop_index("ix_extraction_cells_column_id", table_name="extraction_cells")
    op.drop_index("ix_extraction_cells_paper_id", table_name="extraction_cells")
    op.drop_index("ix_extraction_cells_table_id", table_name="extraction_cells")
    op.drop_table("extraction_cells")

    op.drop_index("ix_extraction_columns_name", table_name="extraction_columns")
    op.drop_index("ix_extraction_columns_table_id", table_name="extraction_columns")
    op.drop_table("extraction_columns")

    op.drop_index("ix_table_papers_status", table_name="table_papers")
    op.drop_index("ix_table_papers_paper_id", table_name="table_papers")
    op.drop_index("ix_table_papers_table_id", table_name="table_papers")
    op.drop_table("table_papers")

    op.drop_index("ix_paper_tags_tag_id", table_name="paper_tags")
    op.drop_index("ix_paper_tags_paper_id", table_name="paper_tags")
    op.drop_table("paper_tags")

    op.drop_index("ix_paper_chunks_embedding_hash", table_name="paper_chunks")
    op.drop_index("ix_paper_chunks_parser_hash", table_name="paper_chunks")
    op.drop_index("ix_paper_chunks_page_start", table_name="paper_chunks")
    op.drop_index("ix_paper_chunks_paper_id", table_name="paper_chunks")
    op.drop_table("paper_chunks")

    op.drop_index("ix_tags_normalized_name", table_name="tags")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_table("tags")

    op.drop_index("ix_extraction_tables_name", table_name="extraction_tables")
    op.drop_table("extraction_tables")

    op.drop_index("ix_papers_index_status", table_name="papers")
    op.drop_index("ix_papers_parse_status", table_name="papers")
    op.drop_index("ix_papers_content_hash", table_name="papers")
    op.drop_index("ix_papers_citation_count", table_name="papers")
    op.drop_index("ix_papers_venue", table_name="papers")
    op.drop_index("ix_papers_year", table_name="papers")
    op.drop_index("ix_papers_doi", table_name="papers")
    op.drop_index("ix_papers_title", table_name="papers")
    op.drop_table("papers")
