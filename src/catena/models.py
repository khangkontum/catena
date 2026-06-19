from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel

DEFAULT_TABLE_NAME = "Default"


class Status:
    NEW = "new"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    PARSED = "parsed"
    INDEXED = "indexed"
    ANSWERED = "answered"
    NOT_REPORTED = "not_reported"
    UNCERTAIN = "uncertain"
    INCLUDED = "included"
    EXCLUDED = "excluded"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Paper(SQLModel, table=True):
    __tablename__ = "papers"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    source_path: str
    stored_pdf_path: str | None = None
    doi: str | None = Field(default=None, index=True)
    url: str | None = None
    authors_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    year: int | None = Field(default=None, index=True)
    venue: str | None = Field(default=None, index=True)
    publication_date: str | None = None
    citation_count: int | None = Field(default=None, index=True)
    abstract: str | None = None
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    content_hash: str | None = Field(default=None, index=True)
    parse_status: str = Field(default=Status.NEW, index=True)
    index_status: str = Field(default=Status.NEW, index=True)
    parse_error: str | None = None
    docling_json_path: str | None = None
    markdown_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PaperChunk(SQLModel, table=True):
    __tablename__ = "paper_chunks"
    __table_args__ = (UniqueConstraint("paper_id", "chunk_index", name="uq_chunk_paper_index"),)

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="papers.id", index=True)
    chunk_index: int
    text: str
    page_start: int | None = Field(default=None, index=True)
    page_end: int | None = None
    heading: str | None = None
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    embedding_model: str | None = None
    parser_hash: str | None = Field(default=None, index=True)
    embedding_hash: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class PaperSimilarity(SQLModel, table=True):
    __tablename__ = "paper_similarities"
    __table_args__ = (
        UniqueConstraint("paper_id_a", "paper_id_b", name="uq_paper_similarity_pair"),
    )

    id: int | None = Field(default=None, primary_key=True)
    paper_id_a: int = Field(foreign_key="papers.id", index=True)
    paper_id_b: int = Field(foreign_key="papers.id", index=True)
    score: float = Field(index=True)
    cosine_similarity: float
    algorithm: str = Field(index=True)
    embedding_model: str | None = Field(default=None, index=True)
    embedding_hash: str | None = Field(default=None, index=True)
    details_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExtractionTable(SQLModel, table=True):
    __tablename__ = "extraction_tables"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = None
    # Resolved absolute path of the source folder for `papers add-dir` imports.
    # Acts as the durable identity of an import table: the same folder always maps
    # to the same table, so re-running an import is idempotent. Null for tables
    # created any other way (Default, `tables create`, filtered tables).
    source_path: str | None = Field(default=None, index=True)
    source_filter_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_tag_normalized_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    normalized_name: str = Field(index=True)
    color: str | None = None
    description: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PaperTag(SQLModel, table=True):
    __tablename__ = "paper_tags"
    __table_args__ = (UniqueConstraint("paper_id", "tag_id", name="uq_paper_tag"),)

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="papers.id", index=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class TablePaper(SQLModel, table=True):
    __tablename__ = "table_papers"
    __table_args__ = (UniqueConstraint("table_id", "paper_id", name="uq_table_paper"),)

    id: int | None = Field(default=None, primary_key=True)
    table_id: int = Field(foreign_key="extraction_tables.id", index=True)
    paper_id: int = Field(foreign_key="papers.id", index=True)
    status: str = Field(default=Status.INCLUDED, index=True)
    notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExtractionColumn(SQLModel, table=True):
    __tablename__ = "extraction_columns"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_column_table_name"),)

    id: int | None = Field(default=None, primary_key=True)
    table_id: int = Field(foreign_key="extraction_tables.id", index=True)
    name: str = Field(index=True)
    prompt: str
    output_type: str = "text"
    retrieval_query: str | None = None
    top_k: int | None = None
    model: str | None = None
    output_schema_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExtractionCell(SQLModel, table=True):
    __tablename__ = "extraction_cells"
    __table_args__ = (
        UniqueConstraint("table_id", "paper_id", "column_id", name="uq_cell_table_paper_column"),
    )

    id: int | None = Field(default=None, primary_key=True)
    table_id: int = Field(foreign_key="extraction_tables.id", index=True)
    paper_id: int = Field(foreign_key="papers.id", index=True)
    column_id: int = Field(foreign_key="extraction_columns.id", index=True)
    status: str = Field(default=Status.QUEUED, index=True)
    answer_text: str | None = None
    value_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    evidence_json: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    confidence: str | None = None
    raw_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
