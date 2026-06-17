from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from catena.config import Settings
from catena.db import init_db, make_engine, session_scope
from catena.embeddings import EmbeddingClient
from catena.extraction import ExtractionService
from catena.filters import PaperFilter, matches_filter, normalize_tag, sort_papers
from catena.metadata import PaperMetadata, fetch_paper_metadata
from catena.models import (
    DEFAULT_TABLE_NAME,
    ExtractionCell,
    ExtractionColumn,
    ExtractionTable,
    Paper,
    PaperChunk,
    PaperSimilarity,
    PaperTag,
    Status,
    TablePaper,
    Tag,
    utcnow,
)
from catena.parsing import PARSER_HASH, parse_pdf
from catena.qa import OneOffAnswer, QuestionAnswerService
from catena.similarity import SimilarityService, SimilarPaper
from catena.util import copy_pdf, safe_title_from_path, sha256_file, sha256_json, write_json
from catena.vector import LanceIndex


class CatenaLibrary:
    """High-level application service for global papers and many extraction tables."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.engine = make_engine(self.settings)

    def init(self) -> None:
        self.settings.ensure_dirs()
        init_db(self.engine)

    def default_table_id(self) -> int:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            table = session.exec(
                select(ExtractionTable).where(ExtractionTable.name == DEFAULT_TABLE_NAME)
            ).first()
            if table is None or table.id is None:
                raise RuntimeError("Default extraction table was not initialized")
            return table.id

    def create_table(self, name: str, description: str | None = None) -> ExtractionTable:
        self.init()
        with session_scope(self.engine) as session:
            table = ExtractionTable(name=name, description=description)
            session.add(table)
            session.commit()
            session.refresh(table)
            return table

    def tables(self) -> list[ExtractionTable]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(select(ExtractionTable).order_by(ExtractionTable.id)).all())

    def create_table_from_filter(
        self,
        name: str,
        paper_filter: PaperFilter,
        *,
        description: str | None = None,
    ) -> tuple[ExtractionTable, list[Paper]]:
        """Create a table from current global papers matching a saved filter."""

        self.init()
        with session_scope(self.engine) as session:
            table = ExtractionTable(
                name=name,
                description=description,
                source_filter_json=paper_filter.to_json(),
            )
            session.add(table)
            session.commit()
            session.refresh(table)
            if table.id is None:
                raise RuntimeError("Created extraction table has no id")
            papers = self._filter_papers_in_session(session, paper_filter)
            for paper in papers:
                if paper.id is not None:
                    _add_table_paper_if_missing(session, table.id, paper.id)
            session.commit()
            return table, papers

    def refresh_table_from_filter(self, table_id: int, *, prune: bool = False) -> list[Paper]:
        """Refresh table membership from its saved filter.

        By default this only adds newly matching papers. With prune=True, papers that no
        longer match are removed from the table along with their table-specific cells.
        """

        self.init()
        with session_scope(self.engine) as session:
            table = session.get(ExtractionTable, table_id)
            if table is None:
                raise ValueError(f"Extraction table {table_id} not found")
            if not table.source_filter_json:
                raise ValueError(f"Extraction table {table_id} has no saved filter")
            paper_filter = PaperFilter(**table.source_filter_json)
            papers = self._filter_papers_in_session(session, paper_filter)
            matching_ids = {paper.id for paper in papers if paper.id is not None}
            for paper_id in matching_ids:
                _add_table_paper_if_missing(session, table_id, paper_id or 0)
                self._ensure_cells_for_table_paper(session, table_id, paper_id or 0)
            if prune:
                memberships = session.exec(
                    select(TablePaper).where(TablePaper.table_id == table_id)
                ).all()
                for membership in memberships:
                    if membership.paper_id not in matching_ids:
                        session.exec(
                            delete(ExtractionCell).where(
                                ExtractionCell.table_id == table_id,
                                ExtractionCell.paper_id == membership.paper_id,
                            )
                        )
                        session.delete(membership)
            session.commit()
            return papers

    async def add_pdf(
        self,
        path: Path,
        *,
        title: str | None = None,
        doi: str | None = None,
        url: str | None = None,
        table_id: int | None = None,
    ) -> Paper:
        """Add a PDF to the global paper library, optionally attaching it to a table.

        Parsing, chunking, embedding, and LanceDB indexing happen once per global paper.
        Adding that paper to more tables later only creates table-specific queued cells.
        """

        self.init()
        source = path.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        if source.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file, got {source}")
        content_hash = sha256_file(source)

        with session_scope(self.engine) as session:
            existing = session.exec(
                select(Paper).where(Paper.content_hash == content_hash)
            ).first()
            if existing is not None:
                paper_id = existing.id
            else:
                paper = Paper(
                    title=title or safe_title_from_path(source),
                    source_path=str(source),
                    doi=doi,
                    url=url,
                    content_hash=content_hash,
                    parse_status=Status.RUNNING,
                    index_status=Status.QUEUED,
                )
                session.add(paper)
                session.commit()
                session.refresh(paper)
                paper_id = paper.id
            if paper_id is None:
                raise RuntimeError("Paper has no id")

        if existing is not None:
            if table_id is not None:
                self.add_paper_to_table(table_id, paper_id)
            return existing

        try:
            paper_dir = self.settings.papers_dir / str(paper_id)
            stored_pdf = copy_pdf(source, paper_dir)
            parsed = parse_pdf(stored_pdf)
            markdown_path = paper_dir / "document.md"
            json_path = paper_dir / "docling.json"
            markdown_path.write_text(parsed.markdown, encoding="utf-8")
            write_json(json_path, parsed.docling_json)

            with session_scope(self.engine) as session:
                paper = session.get(Paper, paper_id)
                if paper is None:
                    raise RuntimeError("Paper disappeared during ingestion")
                paper.stored_pdf_path = str(stored_pdf)
                paper.docling_json_path = str(json_path)
                paper.markdown_path = str(markdown_path)
                paper.parse_status = Status.PARSED
                paper.parse_error = None
                paper.updated_at = utcnow()
                session.add(paper)
                session.exec(delete(PaperChunk).where(PaperChunk.paper_id == paper.id))
                chunks = [
                    PaperChunk(
                        paper_id=paper.id or 0,
                        chunk_index=chunk.index,
                        text=chunk.text,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        heading=chunk.heading,
                        metadata_json=chunk.metadata,
                        parser_hash=PARSER_HASH,
                    )
                    for chunk in parsed.chunks
                ]
                session.add_all(chunks)
                session.commit()
                for chunk in chunks:
                    session.refresh(chunk)
                session.refresh(paper)

            await self.index_paper(paper_id)
            if table_id is not None:
                self.add_paper_to_table(table_id, paper_id)
            with session_scope(self.engine) as session:
                stored = session.get(Paper, paper_id)
                if stored is None:
                    raise RuntimeError("Paper disappeared after indexing")
                return stored
        except Exception as exc:
            with session_scope(self.engine) as session:
                failed = session.get(Paper, paper_id)
                if failed is not None:
                    failed.parse_status = Status.FAILED
                    failed.index_status = Status.FAILED
                    failed.parse_error = str(exc)
                    failed.updated_at = utcnow()
                    session.add(failed)
            raise

    async def index_paper(self, paper_id: int) -> None:
        self.settings.require_gateway()
        with session_scope(self.engine) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")
            chunks = session.exec(
                select(PaperChunk)
                .where(PaperChunk.paper_id == paper_id)
                .order_by(PaperChunk.chunk_index)
            ).all()
            if not chunks:
                raise ValueError(f"Paper {paper_id} has no parsed chunks")
            paper.index_status = Status.RUNNING
            paper.updated_at = utcnow()
            session.add(paper)
            session.commit()

        texts = [chunk.text for chunk in chunks]
        vectors = await EmbeddingClient(self.settings).embed_texts(texts)
        embedding_hash = sha256_json(
            {
                "model": self.settings.embedding_model,
                "base_url": self.settings.gateway_base_url,
                "parser_hash": PARSER_HASH,
            }
        )

        with session_scope(self.engine) as session:
            persisted_chunks = session.exec(
                select(PaperChunk)
                .where(PaperChunk.paper_id == paper_id)
                .order_by(PaperChunk.chunk_index)
            ).all()
            for chunk in persisted_chunks:
                chunk.embedding_model = self.settings.embedding_model
                chunk.embedding_hash = embedding_hash
                session.add(chunk)
            session.commit()
            for chunk in persisted_chunks:
                session.refresh(chunk)
            LanceIndex(self.settings).upsert_chunks(persisted_chunks, vectors)
            paper = session.get(Paper, paper_id)
            if paper is not None:
                paper.index_status = Status.INDEXED
                paper.updated_at = utcnow()
                session.add(paper)

    def add_paper_to_table(self, table_id: int, paper_id: int) -> TablePaper:
        self.init()
        with session_scope(self.engine) as session:
            table = session.get(ExtractionTable, table_id)
            paper = session.get(Paper, paper_id)
            if table is None:
                raise ValueError(f"Extraction table {table_id} not found")
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")

            table_paper = session.exec(
                select(TablePaper).where(
                    TablePaper.table_id == table_id,
                    TablePaper.paper_id == paper_id,
                )
            ).first()
            if table_paper is None:
                table_paper = TablePaper(table_id=table_id, paper_id=paper_id)
                session.add(table_paper)
                session.commit()
                session.refresh(table_paper)
            self._ensure_cells_for_table_paper(session, table_id, paper_id)
            session.refresh(table_paper)
            return table_paper

    def add_column(
        self,
        name: str,
        prompt: str,
        *,
        table_id: int,
        output_type: str = "text",
        retrieval_query: str | None = None,
        top_k: int | None = None,
    ) -> ExtractionColumn:
        self.init()
        with session_scope(self.engine) as session:
            table = session.get(ExtractionTable, table_id)
            if table is None:
                raise ValueError(f"Extraction table {table_id} not found")
            column = ExtractionColumn(
                table_id=table_id,
                name=name,
                prompt=prompt,
                output_type=output_type,
                retrieval_query=retrieval_query,
                top_k=top_k,
                model=self.settings.llm_model,
            )
            session.add(column)
            session.commit()
            session.refresh(column)
            self._ensure_cells_for_column(session, column.id or 0)
            session.refresh(column)
            return column

    async def run_pending(
        self,
        *,
        table_id: int | None = None,
        column_id: int | None = None,
        paper_id: int | None = None,
        limit: int | None = None,
        retry_failed: bool = False,
    ) -> list[ExtractionCell]:
        self.init()
        statuses = [Status.QUEUED]
        if retry_failed:
            statuses.append(Status.FAILED)
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(ExtractionCell).where(ExtractionCell.status.in_(statuses))
            if table_id is not None:
                statement = statement.where(ExtractionCell.table_id == table_id)
            if column_id is not None:
                statement = statement.where(ExtractionCell.column_id == column_id)
            if paper_id is not None:
                statement = statement.where(ExtractionCell.paper_id == paper_id)
            statement = statement.order_by(ExtractionCell.created_at)
            if limit is not None:
                statement = statement.limit(limit)
            cells = session.exec(statement).all()
            ids = [cell.id for cell in cells if cell.id is not None]

        service = ExtractionService(self.settings)
        results: list[ExtractionCell] = []
        for cell_id in ids:
            with Session(self.engine, expire_on_commit=False) as session:
                results.append(await service.extract_cell(session, cell_id))
        return results

    async def ask(
        self,
        question: str,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        top_k: int | None = None,
    ) -> OneOffAnswer:
        self.init()
        service = QuestionAnswerService(self.settings)
        with Session(self.engine, expire_on_commit=False) as session:
            return await service.ask(
                session,
                question,
                paper_ids=paper_ids,
                table_id=table_id,
                top_k=top_k,
            )

    def compute_similarities(
        self,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
    ) -> list[PaperSimilarity]:
        self.init()
        service = SimilarityService(self.settings)
        with session_scope(self.engine) as session:
            return service.compute(session, paper_ids=paper_ids, table_id=table_id)

    def similar_papers(
        self,
        paper_id: int,
        *,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SimilarPaper]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            return SimilarityService.similar_papers(
                session,
                paper_id,
                limit=limit,
                min_score=min_score,
            )

    async def enrich_paper(self, paper_id: int) -> Paper:
        """Fetch free metadata for a paper from OpenAlex/Semantic Scholar."""

        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")
            title = paper.title
            doi = paper.doi
        metadata = await fetch_paper_metadata(title=title, doi=doi)
        if metadata is None:
            raise RuntimeError(f"No metadata found for paper {paper_id}")
        return self.apply_metadata(paper_id, metadata)

    def apply_metadata(self, paper_id: int, metadata: PaperMetadata) -> Paper:
        self.init()
        with session_scope(self.engine) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")
            if metadata.title:
                paper.title = metadata.title
            if metadata.doi:
                paper.doi = metadata.doi
            if metadata.year is not None:
                paper.year = metadata.year
            if metadata.venue:
                paper.venue = metadata.venue
            if metadata.publication_date:
                paper.publication_date = metadata.publication_date
            if metadata.citation_count is not None:
                paper.citation_count = metadata.citation_count
            if metadata.abstract:
                paper.abstract = metadata.abstract
            if metadata.authors:
                paper.authors_json = metadata.authors
            if metadata.url:
                paper.url = metadata.url
            paper.metadata_json = _merge_dicts(
                paper.metadata_json,
                {
                    "pdf_url": metadata.pdf_url,
                    "sources": metadata.sources,
                },
            )
            paper.updated_at = utcnow()
            session.add(paper)
            session.commit()
            session.refresh(paper)
            return paper

    def set_paper_metadata(
        self,
        paper_id: int,
        *,
        year: int | None = None,
        venue: str | None = None,
        citation_count: int | None = None,
        doi: str | None = None,
        abstract: str | None = None,
    ) -> Paper:
        self.init()
        with session_scope(self.engine) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")
            if year is not None:
                paper.year = year
            if venue is not None:
                paper.venue = venue
            if citation_count is not None:
                paper.citation_count = citation_count
            if doi is not None:
                paper.doi = doi
            if abstract is not None:
                paper.abstract = abstract
            paper.updated_at = utcnow()
            session.add(paper)
            session.commit()
            session.refresh(paper)
            return paper

    def create_tag(
        self,
        name: str,
        *,
        color: str | None = None,
        description: str | None = None,
    ) -> Tag:
        self.init()
        normalized_name = normalize_tag(name)
        if not normalized_name:
            raise ValueError("Tag name cannot be empty")
        with session_scope(self.engine) as session:
            tag = session.exec(
                select(Tag).where(Tag.normalized_name == normalized_name)
            ).first()
            if tag is None:
                tag = Tag(
                    name=name.strip(),
                    normalized_name=normalized_name,
                    color=color,
                    description=description,
                )
                session.add(tag)
            else:
                if color is not None:
                    tag.color = color
                if description is not None:
                    tag.description = description
                tag.updated_at = utcnow()
                session.add(tag)
            session.commit()
            session.refresh(tag)
            return tag

    def tags(self) -> list[Tag]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(select(Tag).order_by(Tag.name)).all())

    def tag_paper(self, paper_id: int, tag_name: str) -> Tag:
        self.init()
        with session_scope(self.engine) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise ValueError(f"Paper {paper_id} not found")
            tag = _get_or_create_tag(session, tag_name)
            if tag.id is None:
                raise RuntimeError("Tag has no id")
            existing = session.exec(
                select(PaperTag).where(PaperTag.paper_id == paper_id, PaperTag.tag_id == tag.id)
            ).first()
            if existing is None:
                session.add(PaperTag(paper_id=paper_id, tag_id=tag.id))
            session.commit()
            session.refresh(tag)
            return tag

    def untag_paper(self, paper_id: int, tag_name: str) -> None:
        self.init()
        normalized_name = normalize_tag(tag_name)
        with session_scope(self.engine) as session:
            tag = session.exec(
                select(Tag).where(Tag.normalized_name == normalized_name)
            ).first()
            if tag is None or tag.id is None:
                return
            links = session.exec(
                select(PaperTag).where(PaperTag.paper_id == paper_id, PaperTag.tag_id == tag.id)
            ).all()
            for link in links:
                session.delete(link)

    def paper_tag_names(self, paper_id: int) -> list[str]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            return _paper_tag_names(session, paper_id)

    def filter_papers(self, paper_filter: PaperFilter) -> list[Paper]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            return self._filter_papers_in_session(session, paper_filter)

    def papers(self, *, table_id: int | None = None) -> list[Paper]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            if table_id is None:
                return list(session.exec(select(Paper).order_by(Paper.id)).all())
            memberships = session.exec(
                select(TablePaper)
                .where(TablePaper.table_id == table_id)
                .order_by(TablePaper.created_at)
            ).all()
            ids = [membership.paper_id for membership in memberships]
            if not ids:
                return []
            papers = session.exec(select(Paper).where(Paper.id.in_(ids))).all()  # type: ignore[union-attr]
            by_id = {paper.id: paper for paper in papers}
            return [by_id[paper_id] for paper_id in ids if paper_id in by_id]

    def columns(self, *, table_id: int | None = None) -> list[ExtractionColumn]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(ExtractionColumn).order_by(ExtractionColumn.id)
            if table_id is not None:
                statement = statement.where(ExtractionColumn.table_id == table_id)
            return list(session.exec(statement).all())

    def cells(self, *, table_id: int | None = None) -> list[ExtractionCell]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(ExtractionCell).order_by(
                ExtractionCell.table_id,
                ExtractionCell.paper_id,
                ExtractionCell.column_id,
            )
            if table_id is not None:
                statement = statement.where(ExtractionCell.table_id == table_id)
            return list(session.exec(statement).all())

    def table_rows(
        self,
        table_id: int,
    ) -> tuple[ExtractionTable, list[ExtractionColumn], list[dict[str, str]]]:
        self.init()
        with Session(self.engine, expire_on_commit=False) as session:
            table = session.get(ExtractionTable, table_id)
            if table is None:
                raise ValueError(f"Extraction table {table_id} not found")
            columns = list(
                session.exec(
                    select(ExtractionColumn)
                    .where(ExtractionColumn.table_id == table_id)
                    .order_by(ExtractionColumn.id)
                ).all()
            )
            memberships = session.exec(
                select(TablePaper)
                .where(TablePaper.table_id == table_id)
                .order_by(TablePaper.created_at)
            ).all()
            paper_ids = [membership.paper_id for membership in memberships]
            if paper_ids:
                papers = session.exec(
                    select(Paper).where(Paper.id.in_(paper_ids))  # type: ignore[union-attr]
                ).all()
            else:
                papers = []
            cell_statement = select(ExtractionCell).where(ExtractionCell.table_id == table_id)
            cells = list(session.exec(cell_statement).all())
            papers_by_id = {paper.id: paper for paper in papers}
            cells_by_key = {(cell.paper_id, cell.column_id): cell for cell in cells}

        rows: list[dict[str, str]] = []
        for paper_id in paper_ids:
            paper = papers_by_id.get(paper_id)
            if paper is None:
                continue
            row: dict[str, str] = {
                "id": str(paper.id),
                "title": paper.title,
                "parse_status": paper.parse_status,
                "index_status": paper.index_status,
            }
            for column in columns:
                cell = cells_by_key.get((paper.id or 0, column.id or 0))
                if cell is None:
                    row[column.name] = ""
                elif cell.status in {Status.ANSWERED, Status.NOT_REPORTED, Status.UNCERTAIN}:
                    row[column.name] = cell.answer_text or cell.status
                else:
                    row[column.name] = cell.status
            rows.append(row)
        return table, columns, rows

    def _filter_papers_in_session(self, session: Session, paper_filter: PaperFilter) -> list[Paper]:
        papers = list(session.exec(select(Paper)).all())
        matching = [
            paper
            for paper in papers
            if paper.id is not None
            and matches_filter(paper, set(_paper_tag_names(session, paper.id)), paper_filter)
        ]
        return sort_papers(matching, paper_filter)

    def _ensure_cells_for_table_paper(self, session: Session, table_id: int, paper_id: int) -> None:
        columns = session.exec(
            select(ExtractionColumn).where(ExtractionColumn.table_id == table_id)
        ).all()
        for column in columns:
            _add_cell_if_missing(session, table_id, paper_id, column.id or 0)
        session.commit()

    def _ensure_cells_for_column(self, session: Session, column_id: int) -> None:
        column = session.get(ExtractionColumn, column_id)
        if column is None:
            raise ValueError(f"Extraction column {column_id} not found")
        memberships = session.exec(
            select(TablePaper).where(TablePaper.table_id == column.table_id)
        ).all()
        for membership in memberships:
            _add_cell_if_missing(session, column.table_id, membership.paper_id, column_id)
        session.commit()


def _add_cell_if_missing(session: Session, table_id: int, paper_id: int, column_id: int) -> None:
    if not table_id or not paper_id or not column_id:
        return
    existing = session.exec(
        select(ExtractionCell).where(
            ExtractionCell.table_id == table_id,
            ExtractionCell.paper_id == paper_id,
            ExtractionCell.column_id == column_id,
        )
    ).first()
    if existing is not None:
        return
    session.add(
        ExtractionCell(
            table_id=table_id,
            paper_id=paper_id,
            column_id=column_id,
            status=Status.QUEUED,
        )
    )
    try:
        session.flush()
    except IntegrityError:
        session.rollback()


def _add_table_paper_if_missing(session: Session, table_id: int, paper_id: int) -> None:
    if not table_id or not paper_id:
        return
    existing = session.exec(
        select(TablePaper).where(
            TablePaper.table_id == table_id,
            TablePaper.paper_id == paper_id,
        )
    ).first()
    if existing is None:
        session.add(TablePaper(table_id=table_id, paper_id=paper_id))


def _get_or_create_tag(session: Session, tag_name: str) -> Tag:
    normalized_name = normalize_tag(tag_name)
    if not normalized_name:
        raise ValueError("Tag name cannot be empty")
    tag = session.exec(select(Tag).where(Tag.normalized_name == normalized_name)).first()
    if tag is not None:
        return tag
    tag = Tag(name=tag_name.strip(), normalized_name=normalized_name)
    session.add(tag)
    session.flush()
    session.refresh(tag)
    return tag


def _paper_tag_names(session: Session, paper_id: int) -> list[str]:
    rows = session.exec(
        select(Tag)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper_id)
        .order_by(Tag.name)
    ).all()
    return [tag.name for tag in rows]


def _merge_dicts(original: dict[str, Any] | None, update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(original or {})
    for key, value in update.items():
        if value is not None:
            merged[key] = value
    return merged
