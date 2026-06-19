from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
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
from catena.parsing import PARSER_HASH, ParsedDocument, parse_pdfs
from catena.qa import AskMode, OneOffAnswer, QuestionAnswerService
from catena.search import SearchMode, SearchResult, SearchService, rebuild_search_index
from catena.similarity import SimilarityService, SimilarPaper
from catena.util import copy_pdf, safe_title_from_path, sha256_file, sha256_json, write_json
from catena.vector import LanceIndex


@dataclass(frozen=True)
class RegisteredPaper:
    """Row produced by `register_pdfs`: a paper id plus whether it was newly created."""

    paper_id: int
    title: str
    is_new: bool


@dataclass(frozen=True)
class IngestResult:
    """Outcome of parsing/indexing one paper in `ingest_papers`."""

    paper_id: int
    parse_status: str
    index_status: str
    error: str | None = None


@dataclass(frozen=True)
class IngestProgress:
    """Diagnostic event emitted while papers are parsed and indexed."""

    step: str
    message: str
    paper_id: int | None = None
    current: int | None = None
    total: int | None = None
    error: str | None = None


IngestProgressCallback = Callable[[IngestProgress], None]


class CatenaLibrary:
    """High-level application service for global papers and many extraction tables."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.engine = make_engine(self.settings)
        self._init_lock = RLock()
        self._initialized = False

    def init(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            self.settings.ensure_dirs()
            init_db(self.engine)
            self._initialized = True

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
        """Add one PDF globally, optionally attaching it to a table."""

        papers = await self.add_pdfs(
            [path],
            title=title,
            doi=doi,
            url=url,
            table_id=table_id,
        )
        if not papers:
            raise RuntimeError(f"No paper was added for {path}")
        paper = papers[0]
        if paper.parse_status == Status.FAILED:
            raise RuntimeError(paper.parse_error or f"Failed to add {path}")
        return paper

    async def add_pdfs(
        self,
        paths: list[Path],
        *,
        title: str | None = None,
        doi: str | None = None,
        url: str | None = None,
        table_id: int | None = None,
    ) -> list[Paper]:
        """Add PDFs globally with one Docling batch conversion for new files.

        Parsing, chunking, embedding, and LanceDB indexing happen once per global paper.
        Existing papers are only attached to the requested table. New PDFs in this batch
        share one preloaded Docling converter and one `convert_all()` pass instead of
        recreating the Docling pipeline per file.
        """

        registered = self.register_pdfs(
            paths,
            table_id=table_id,
            title=title,
            doi=doi,
            url=url,
        )
        new_ids = [item.paper_id for item in registered if item.is_new]
        if new_ids:
            await self.ingest_papers(paper_ids=new_ids)
        return self._papers_by_ids([item.paper_id for item in registered])

    def get_or_create_table_for_path(
        self,
        source_path: Path,
        *,
        name: str,
        description: str | None = None,
    ) -> ExtractionTable:
        """Return the table bound to an exact resolved source path, creating it if absent.

        Folder imports use the resolved absolute directory path as the durable identity
        of their extraction table, so re-running an import against the same folder reuses
        the same table (and content-hash-deduped papers) instead of creating a duplicate.
        Different folders always map to different tables, eliminating name collisions.
        """

        self.init()
        resolved = str(source_path)
        with session_scope(self.engine) as session:
            existing = session.exec(
                select(ExtractionTable)
                .where(ExtractionTable.source_path == resolved)
                .order_by(ExtractionTable.id)
            ).first()
            if existing is not None:
                return existing
            table = ExtractionTable(
                name=name,
                description=description,
                source_path=resolved,
            )
            session.add(table)
            session.commit()
            session.refresh(table)
            return table

    def register_pdfs(
        self,
        paths: list[Path],
        *,
        table_id: int | None = None,
        title: str | None = None,
        doi: str | None = None,
        url: str | None = None,
    ) -> list[RegisteredPaper]:
        """Validate, dedup by content hash, create QUEUED Paper rows, copy PDFs into
        storage, and attach them to the table. No parsing or embedding happens here.

        Idempotent on content hash: an already-registered paper is reused (``is_new=False``)
        rather than reprocessed. Pair with `ingest_papers` to parse/index new papers.
        """

        self.init()
        sources = [_validated_pdf_path(path) for path in paths]
        if not sources:
            return []
        if len(sources) > 1 and any(item is not None for item in (title, doi, url)):
            raise ValueError("title, doi, and url overrides are only supported for one PDF")

        registered: list[RegisteredPaper] = []
        with session_scope(self.engine) as session:
            for source in sources:
                content_hash = sha256_file(source)
                existing = session.exec(
                    select(Paper).where(Paper.content_hash == content_hash)
                ).first()
                if existing is not None:
                    if existing.id is None:
                        raise RuntimeError("Existing paper has no id")
                    registered.append(RegisteredPaper(existing.id, existing.title, False))
                    continue

                paper = Paper(
                    title=title or safe_title_from_path(source),
                    source_path=str(source),
                    doi=doi,
                    url=url,
                    content_hash=content_hash,
                    parse_status=Status.QUEUED,
                    index_status=Status.QUEUED,
                )
                session.add(paper)
                session.commit()
                session.refresh(paper)
                if paper.id is None:
                    raise RuntimeError("Paper has no id")
                paper_dir = self.settings.papers_dir / str(paper.id)
                stored_pdf = copy_pdf(source, paper_dir)
                paper.stored_pdf_path = str(stored_pdf)
                session.add(paper)
                session.commit()
                registered.append(RegisteredPaper(paper.id, paper.title, True))

        if table_id is not None:
            for item in registered:
                self.add_paper_to_table(table_id, item.paper_id)

        return registered

    async def ingest_papers(
        self,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        retry_failed: bool = False,
        progress: IngestProgressCallback | None = None,
    ) -> list[IngestResult]:
        """Batched Docling parse + embedding index for QUEUED papers (and FAILED if
        ``retry_failed``). Scope by ``paper_ids`` and/or ``table_id``. Papers already
        parsed/indexed are skipped. New papers in one call share a single Docling
        `convert_all()` pass.
        """

        self.init()
        parse_statuses = [Status.QUEUED]
        if retry_failed:
            parse_statuses.append(Status.FAILED)
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(Paper).where(Paper.parse_status.in_(parse_statuses))
            if paper_ids is not None:
                statement = statement.where(Paper.id.in_(paper_ids))  # type: ignore[union-attr]
            if table_id is not None:
                statement = statement.join(
                    TablePaper, TablePaper.paper_id == Paper.id
                ).where(TablePaper.table_id == table_id)
            papers = session.exec(statement).all()

        pending: list[tuple[int, Path]] = []
        results: list[IngestResult] = []
        for paper in papers:
            if paper.id is None:
                continue
            if not paper.stored_pdf_path:
                error = "No stored PDF path; cannot ingest"
                self._mark_paper_failed(paper.id, error)
                results.append(IngestResult(paper.id, Status.FAILED, Status.FAILED, error))
                continue
            pending.append((paper.id, Path(paper.stored_pdf_path)))

        if not pending:
            if progress is not None:
                progress(IngestProgress("queued", "No queued papers to ingest", total=0))
            return results

        total = len(pending)
        if progress is not None:
            progress(IngestProgress("queued", f"Found {total} paper(s) to ingest", total=total))

        self._mark_papers_running([paper_id for paper_id, _ in pending])
        if progress is not None:
            progress(IngestProgress("parse", "Starting Docling batch parse", total=total))

        parsed_results = parse_pdfs([path for _, path in pending])
        if progress is not None:
            progress(IngestProgress("parse", "Finished Docling batch parse", total=total))

        for (paper_id, _stored), result in zip(pending, parsed_results, strict=True):
            current = len(results) + 1
            if result.document is None:
                error = result.error or "Docling conversion failed"
                self._mark_paper_failed(paper_id, error)
                results.append(IngestResult(paper_id, Status.FAILED, Status.FAILED, error))
                if progress is not None:
                    progress(
                        IngestProgress(
                            "parse_failed",
                            f"Paper {paper_id} parse failed",
                            paper_id=paper_id,
                            current=current,
                            total=total,
                            error=error,
                        )
                    )
                continue
            try:
                self._persist_parsed_document(paper_id, result.document)
                if progress is not None:
                    progress(
                        IngestProgress(
                            "parsed",
                            f"Paper {paper_id} parsed",
                            paper_id=paper_id,
                            current=current,
                            total=total,
                        )
                    )
                    progress(
                        IngestProgress(
                            "index",
                            f"Indexing paper {paper_id}",
                            paper_id=paper_id,
                            current=current,
                            total=total,
                        )
                    )
                await self.index_paper(paper_id)
                results.append(IngestResult(paper_id, Status.PARSED, Status.INDEXED))
                if progress is not None:
                    progress(
                        IngestProgress(
                            "indexed",
                            f"Paper {paper_id} indexed",
                            paper_id=paper_id,
                            current=current,
                            total=total,
                        )
                    )
            except Exception as exc:
                self._mark_paper_failed(paper_id, str(exc))
                results.append(IngestResult(paper_id, Status.FAILED, Status.FAILED, str(exc)))
                if progress is not None:
                    progress(
                        IngestProgress(
                            "failed",
                            f"Paper {paper_id} ingest failed",
                            paper_id=paper_id,
                            current=current,
                            total=total,
                            error=str(exc),
                        )
                    )
        if progress is not None:
            failed = sum(1 for result in results if result.parse_status == Status.FAILED)
            progress(
                IngestProgress(
                    "complete",
                    f"Ingest complete: {len(results) - failed} indexed, {failed} failed",
                    current=len(results),
                    total=total,
                )
            )
        return results

    def _persist_parsed_document(
        self,
        paper_id: int,
        parsed: ParsedDocument,
    ) -> None:
        paper_dir = self.settings.papers_dir / str(paper_id)
        markdown_path = paper_dir / "document.md"
        json_path = paper_dir / "docling.json"
        markdown_path.write_text(parsed.markdown, encoding="utf-8")
        write_json(json_path, parsed.docling_json)

        with session_scope(self.engine) as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                raise RuntimeError("Paper disappeared during ingestion")
            paper.docling_json_path = str(json_path)
            paper.markdown_path = str(markdown_path)
            paper.parse_status = Status.PARSED
            paper.parse_error = None
            paper.updated_at = utcnow()
            session.add(paper)
            session.exec(delete(PaperChunk).where(PaperChunk.paper_id == paper_id))
            chunks = [
                PaperChunk(
                    paper_id=paper_id,
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
            rebuild_search_index(session, paper_id=paper_id)

    def _mark_paper_failed(self, paper_id: int, error: str) -> None:
        with session_scope(self.engine) as session:
            failed = session.get(Paper, paper_id)
            if failed is not None:
                failed.parse_status = Status.FAILED
                failed.index_status = Status.FAILED
                failed.parse_error = error
                failed.updated_at = utcnow()
                session.add(failed)

    def _mark_papers_running(self, paper_ids: list[int]) -> None:
        if not paper_ids:
            return
        with session_scope(self.engine) as session:
            papers = session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()  # type: ignore[union-attr]
            for paper in papers:
                paper.parse_status = Status.RUNNING
                paper.index_status = Status.QUEUED
                paper.parse_error = None
                paper.updated_at = utcnow()
                session.add(paper)

    def _papers_by_ids(self, paper_ids: list[int]) -> list[Paper]:
        if not paper_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            papers = session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()  # type: ignore[union-attr]
            papers_by_id = {paper.id: paper for paper in papers}
            return [papers_by_id[paper_id] for paper_id in paper_ids if paper_id in papers_by_id]

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
        concurrency = max(1, self.settings.cell_concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        async def run_cell(cell_id: int) -> ExtractionCell:
            async with semaphore:
                with Session(self.engine, expire_on_commit=False) as session:
                    return await service.extract_cell(session, cell_id)

        if concurrency == 1:
            results: list[ExtractionCell] = []
            for cell_id in ids:
                results.append(await run_cell(cell_id))
            return results
        return list(await asyncio.gather(*(run_cell(cell_id) for cell_id in ids)))

    async def ask(
        self,
        question: str,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        top_k: int | None = None,
        mode: AskMode = "auto",
        max_context_chars: int | None = None,
        batch_size: int | None = None,
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
                mode=mode,
                max_context_chars=max_context_chars,
                batch_size=batch_size,
            )

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode = "auto",
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        top_k: int | None = None,
    ) -> list[SearchResult]:
        self.init()
        service = SearchService(self.settings)
        with Session(self.engine, expire_on_commit=False) as session:
            return await service.search(
                session,
                query,
                mode=mode,
                paper_ids=paper_ids,
                table_id=table_id,
                top_k=top_k,
            )

    def rebuild_search_index(self, *, paper_id: int | None = None) -> None:
        self.init()
        with session_scope(self.engine) as session:
            rebuild_search_index(session, paper_id=paper_id)

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
            rebuild_search_index(session, paper_id=paper_id)
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
            rebuild_search_index(session, paper_id=paper_id)
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


def _validated_pdf_path(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file, got {source}")
    return source


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
