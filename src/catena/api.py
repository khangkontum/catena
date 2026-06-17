from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from catena.config import Settings
from catena.filters import PaperFilter
from catena.library import CatenaLibrary
from catena.models import (
    ExtractionCell,
    ExtractionColumn,
    ExtractionTable,
    Paper,
    PaperSimilarity,
    Tag,
)
from catena.qa import OneOffAnswer
from catena.similarity import SimilarPaper

DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source_path: str
    stored_pdf_path: str | None = None
    doi: str | None = None
    url: str | None = None
    authors_json: list[str] | None = None
    year: int | None = None
    venue: str | None = None
    publication_date: str | None = None
    citation_count: int | None = None
    abstract: str | None = None
    metadata_json: dict[str, Any] | None = None
    content_hash: str | None = None
    parse_status: str
    index_status: str
    parse_error: str | None = None
    docling_json_path: str | None = None
    markdown_path: str | None = None


class PaperWithTagsOut(PaperOut):
    tags: list[str] = Field(default_factory=list)


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    source_filter_json: dict[str, Any] | None = None


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    color: str | None = None
    description: str | None = None


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    table_id: int
    name: str
    prompt: str
    output_type: str
    retrieval_query: str | None = None
    top_k: int | None = None
    model: str | None = None
    output_schema_json: dict[str, Any] | None = None


class CellOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    table_id: int
    paper_id: int
    column_id: int
    status: str
    answer_text: str | None = None
    value_json: dict[str, Any] | None = None
    evidence_json: list[dict[str, Any]] | None = None
    confidence: str | None = None
    raw_json: dict[str, Any] | None = None
    error: str | None = None


class MatrixRowOut(BaseModel):
    paper: PaperWithTagsOut
    cells: dict[str, CellOut]


class MatrixOut(BaseModel):
    table: TableOut
    columns: list[ColumnOut]
    rows: list[MatrixRowOut]


class SimilarityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id_a: int
    paper_id_b: int
    score: float
    cosine_similarity: float
    algorithm: str
    embedding_model: str | None = None
    embedding_hash: str | None = None
    details_json: dict[str, Any] | None = None


class SimilarPaperOut(BaseModel):
    paper: PaperOut
    similarity: SimilarityOut


class TableCreateIn(BaseModel):
    name: str
    description: str | None = None


class FilteredTableCreateIn(TableCreateIn):
    paper_filter: PaperFilter = Field(default_factory=PaperFilter)


class TableAttachPaperIn(BaseModel):
    paper_id: int


class TableRefreshIn(BaseModel):
    prune: bool = False


class ColumnCreateIn(BaseModel):
    table_id: int
    name: str
    prompt: str
    output_type: str = "text"
    retrieval_query: str | None = None
    top_k: int | None = None
    run: bool = False


class TagCreateIn(BaseModel):
    name: str
    color: str | None = None
    description: str | None = None


class TagsAddIn(BaseModel):
    tags: list[str]


class PaperMetadataIn(BaseModel):
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    abstract: str | None = None


class RunIn(BaseModel):
    table_id: int | None = None
    column_id: int | None = None
    paper_id: int | None = None
    limit: int | None = None
    retry_failed: bool = False


class AskIn(BaseModel):
    question: str
    paper_ids: list[int] | None = None
    table_id: int | None = None
    top_k: int | None = None


class AskOut(BaseModel):
    question: str
    paper_ids: list[int]
    answer: str
    evidence: list[dict[str, Any]]
    confidence: str | None = None
    rationale: str | None = None
    raw: dict[str, Any]
    retrieved_chunk_ids: list[int]


class SimilarityComputeIn(BaseModel):
    paper_ids: list[int] | None = None
    table_id: int | None = None


class HealthOut(BaseModel):
    ok: bool
    data_dir: str
    gateway_ready: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    library = CatenaLibrary(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        library.init()
        yield

    app = FastAPI(
        title="catena API",
        version="0.1.0",
        description="Local API for catena paper extraction tables.",
        lifespan=lifespan,
    )
    app.state.library = library
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut(
            ok=True,
            data_dir=str(library.settings.data_dir),
            gateway_ready=library.settings.gateway_ready,
        )

    @app.get("/tables", response_model=list[TableOut])
    def list_tables() -> list[ExtractionTable]:
        return library.tables()

    @app.post("/tables", response_model=TableOut)
    def create_table(payload: TableCreateIn) -> ExtractionTable:
        return library.create_table(payload.name, payload.description)

    @app.post("/tables/from-filter", response_model=MatrixOut)
    def create_table_from_filter(payload: FilteredTableCreateIn) -> MatrixOut:
        table, _ = library.create_table_from_filter(
            payload.name,
            payload.paper_filter,
            description=payload.description,
        )
        if table.id is None:
            raise RuntimeError("Created table has no id")
        return _matrix(library, table.id)

    @app.post("/tables/{table_id}/refresh", response_model=MatrixOut)
    def refresh_table(table_id: int, payload: TableRefreshIn) -> MatrixOut:
        library.refresh_table_from_filter(table_id, prune=payload.prune)
        return _matrix(library, table_id)

    @app.get("/tables/{table_id}/papers", response_model=list[PaperWithTagsOut])
    def list_table_papers(table_id: int) -> list[PaperWithTagsOut]:
        return _papers_with_tags(library, library.papers(table_id=table_id))

    @app.post("/tables/{table_id}/papers", response_model=MatrixOut)
    def attach_paper_to_table(table_id: int, payload: TableAttachPaperIn) -> MatrixOut:
        library.add_paper_to_table(table_id, payload.paper_id)
        return _matrix(library, table_id)

    @app.get("/tables/{table_id}/matrix", response_model=MatrixOut)
    def table_matrix(table_id: int) -> MatrixOut:
        return _matrix(library, table_id)

    @app.get("/papers", response_model=list[PaperWithTagsOut])
    def list_papers(
        table_id: int | None = None,
        tag_all: Annotated[list[str] | None, Query()] = None,
        tag_any: Annotated[list[str] | None, Query()] = None,
        tag_none: Annotated[list[str] | None, Query()] = None,
        untagged: bool = False,
        year_min: int | None = None,
        year_max: int | None = None,
        citations_min: int | None = None,
        citations_max: int | None = None,
        title_contains: str | None = None,
        venue_contains: str | None = None,
        has_doi: bool = False,
        missing_doi: bool = False,
        has_pdf: bool = False,
        parsed_only: bool = False,
        indexed_only: bool = False,
        limit: int | None = None,
        sort_by: str = "created",
        descending: bool = False,
    ) -> list[PaperWithTagsOut]:
        papers = library.papers(table_id=table_id)
        if _has_filter(
            tag_all,
            tag_any,
            tag_none,
            untagged,
            year_min,
            year_max,
            citations_min,
            citations_max,
            title_contains,
            venue_contains,
            has_doi,
            missing_doi,
            has_pdf,
            parsed_only,
            indexed_only,
            limit,
            sort_by,
            descending,
        ):
            paper_filter = PaperFilter(
                tags_all=tag_all or [],
                tags_any=tag_any or [],
                tags_none=tag_none or [],
                untagged=untagged,
                year_min=year_min,
                year_max=year_max,
                citations_min=citations_min,
                citations_max=citations_max,
                title_contains=title_contains,
                venue_contains=venue_contains,
                has_doi=has_doi,
                missing_doi=missing_doi,
                has_pdf=has_pdf,
                parsed_only=parsed_only,
                indexed_only=indexed_only,
                limit=limit,
                sort_by=sort_by,  # type: ignore[arg-type]
                descending=descending,
            )
            matching_ids = {paper.id for paper in library.filter_papers(paper_filter)}
            papers = [paper for paper in papers if paper.id in matching_ids]
        return _papers_with_tags(library, papers)

    @app.post("/papers/upload", response_model=PaperWithTagsOut)
    async def upload_paper(
        pdf: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        doi: Annotated[str | None, Form()] = None,
        url: Annotated[str | None, Form()] = None,
        table_id: Annotated[int | None, Form()] = None,
        no_table: Annotated[bool, Form()] = False,
    ) -> PaperWithTagsOut:
        if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Upload must be a PDF file")
        upload_path = await _store_upload(library.settings.data_dir / "uploads", pdf)
        resolved_table_id = None if no_table else table_id or library.default_table_id()
        paper = await library.add_pdf(
            upload_path,
            title=title,
            doi=doi,
            url=url,
            table_id=resolved_table_id,
        )
        return _paper_with_tags(library, paper)

    @app.patch("/papers/{paper_id}/metadata", response_model=PaperWithTagsOut)
    def set_paper_metadata(paper_id: int, payload: PaperMetadataIn) -> PaperWithTagsOut:
        paper = library.set_paper_metadata(
            paper_id,
            year=payload.year,
            venue=payload.venue,
            citation_count=payload.citation_count,
            doi=payload.doi,
            abstract=payload.abstract,
        )
        return _paper_with_tags(library, paper)

    @app.post("/papers/{paper_id}/enrich", response_model=PaperWithTagsOut)
    async def enrich_paper(paper_id: int) -> PaperWithTagsOut:
        paper = await library.enrich_paper(paper_id)
        return _paper_with_tags(library, paper)

    @app.get("/papers/{paper_id}/tags", response_model=list[str])
    def paper_tags(paper_id: int) -> list[str]:
        return library.paper_tag_names(paper_id)

    @app.post("/papers/{paper_id}/tags", response_model=PaperWithTagsOut)
    def add_tags_to_paper(paper_id: int, payload: TagsAddIn) -> PaperWithTagsOut:
        for tag in payload.tags:
            library.tag_paper(paper_id, tag)
        paper = _get_paper(library, paper_id)
        return _paper_with_tags(library, paper)

    @app.delete("/papers/{paper_id}/tags/{tag_name}", response_model=PaperWithTagsOut)
    def remove_tag_from_paper(paper_id: int, tag_name: str) -> PaperWithTagsOut:
        library.untag_paper(paper_id, tag_name)
        paper = _get_paper(library, paper_id)
        return _paper_with_tags(library, paper)

    @app.get("/papers/{paper_id}/similar", response_model=list[SimilarPaperOut])
    def similar_papers(
        paper_id: int,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SimilarPaperOut]:
        items = library.similar_papers(
            paper_id,
            limit=limit,
            min_score=min_score,
        )
        return [_similar_paper_out(item) for item in items]

    @app.get("/tags", response_model=list[TagOut])
    def list_tags() -> list[Tag]:
        return library.tags()

    @app.post("/tags", response_model=TagOut)
    def create_tag(payload: TagCreateIn) -> Tag:
        return library.create_tag(
            payload.name,
            color=payload.color,
            description=payload.description,
        )

    @app.get("/columns", response_model=list[ColumnOut])
    def list_columns(table_id: int | None = None) -> list[ExtractionColumn]:
        return library.columns(table_id=table_id)

    @app.post("/columns", response_model=ColumnOut)
    async def create_column(payload: ColumnCreateIn) -> ExtractionColumn:
        column = library.add_column(
            payload.name,
            payload.prompt,
            table_id=payload.table_id,
            output_type=payload.output_type,
            retrieval_query=payload.retrieval_query,
            top_k=payload.top_k,
        )
        if payload.run:
            await library.run_pending(table_id=payload.table_id, column_id=column.id)
        return column

    @app.post("/run", response_model=list[CellOut])
    async def run_pending(payload: RunIn) -> list[ExtractionCell]:
        return await library.run_pending(
            table_id=payload.table_id,
            column_id=payload.column_id,
            paper_id=payload.paper_id,
            limit=payload.limit,
            retry_failed=payload.retry_failed,
        )

    @app.post("/ask", response_model=AskOut)
    async def ask(payload: AskIn) -> AskOut:
        answer = await library.ask(
            payload.question,
            paper_ids=payload.paper_ids,
            table_id=payload.table_id,
            top_k=payload.top_k,
        )
        return _answer_out(answer)

    @app.post("/similarity/compute", response_model=list[SimilarityOut])
    def compute_similarity(payload: SimilarityComputeIn) -> list[PaperSimilarity]:
        return library.compute_similarities(paper_ids=payload.paper_ids, table_id=payload.table_id)

    return app


def _cors_origins() -> list[str]:
    value = os.environ.get("CATENA_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def _paper_with_tags(library: CatenaLibrary, paper: Paper) -> PaperWithTagsOut:
    payload = PaperOut.model_validate(paper).model_dump()
    return PaperWithTagsOut(**payload, tags=library.paper_tag_names(paper.id or 0))


def _papers_with_tags(library: CatenaLibrary, papers: list[Paper]) -> list[PaperWithTagsOut]:
    return [_paper_with_tags(library, paper) for paper in papers]


def _cell_out(cell: ExtractionCell) -> CellOut:
    return CellOut.model_validate(cell)


def _matrix(library: CatenaLibrary, table_id: int) -> MatrixOut:
    tables = {table.id: table for table in library.tables()}
    table = tables.get(table_id)
    if table is None:
        raise ValueError(f"Extraction table {table_id} not found")
    columns = library.columns(table_id=table_id)
    papers = library.papers(table_id=table_id)
    cells = library.cells(table_id=table_id)
    cells_by_paper: dict[int, dict[str, CellOut]] = {}
    for cell in cells:
        cells_by_paper.setdefault(cell.paper_id, {})[str(cell.column_id)] = _cell_out(cell)
    rows = [
        MatrixRowOut(
            paper=_paper_with_tags(library, paper),
            cells=cells_by_paper.get(paper.id or 0, {}),
        )
        for paper in papers
    ]
    return MatrixOut(table=TableOut.model_validate(table), columns=columns, rows=rows)


def _get_paper(library: CatenaLibrary, paper_id: int) -> Paper:
    for paper in library.papers():
        if paper.id == paper_id:
            return paper
    raise ValueError(f"Paper {paper_id} not found")


async def _store_upload(directory: Path, upload: UploadFile) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "paper.pdf").name
    destination = directory / f"{uuid4().hex}-{safe_name}"
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            handle.write(chunk)
    return destination


def _answer_out(answer: OneOffAnswer) -> AskOut:
    return AskOut(
        question=answer.question,
        paper_ids=answer.paper_ids,
        answer=answer.answer,
        evidence=answer.evidence,
        confidence=answer.confidence,
        rationale=answer.rationale,
        raw=answer.raw,
        retrieved_chunk_ids=answer.retrieved_chunk_ids,
    )


def _similarity_out(similarity: PaperSimilarity) -> SimilarityOut:
    return SimilarityOut.model_validate(similarity)


def _similar_paper_out(item: SimilarPaper) -> SimilarPaperOut:
    return SimilarPaperOut(
        paper=PaperOut.model_validate(item.paper),
        similarity=_similarity_out(item.similarity),
    )


def _has_filter(*values: object) -> bool:
    return any(value not in (None, False, [], "created") for value in values)
