import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import Session, select

from catena.config import Settings
from catena.filters import PaperFilter
from catena.library import CatenaLibrary
from catena.models import (
    ExtractionCell,
    Paper,
    PaperChunk,
    PaperSimilarity,
    Status,
    TablePaper,
)
from catena.parsing import ParsedChunk, ParsedDocument, ParsedPdfResult
from catena.qa import QuestionAnswerService
from catena.vector import LanceIndex


def test_init_runs_alembic_and_creates_default_table(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()

    with sqlite3.connect(tmp_path / "catena.sqlite") as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()

    assert revision == ("20260619_0004",)
    assert library.default_table_id() == 1


def test_init_is_thread_safe_for_parallel_first_requests(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))

    def load_tables() -> int:
        return len(library.tables())

    with ThreadPoolExecutor(max_workers=8) as executor:
        counts = list(executor.map(lambda _: load_tables(), range(16)))

    assert counts == [1] * 16


@pytest.mark.asyncio
async def test_add_pdfs_uses_one_batch_docling_parse(monkeypatch, tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    table_id = library.default_table_id()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    parse_calls: list[list[Path]] = []

    def fake_parse_pdfs(paths: list[Path]) -> list[ParsedPdfResult]:
        parse_calls.append(paths)
        return [
            ParsedPdfResult(
                path=path,
                document=ParsedDocument(
                    markdown=f"# {path.stem}",
                    docling_json={"name": path.name},
                    chunks=[
                        ParsedChunk(
                            index=0,
                            text=f"chunk for {path.name}",
                            page_start=1,
                            page_end=1,
                            heading=None,
                            metadata={},
                        )
                    ],
                ),
            )
            for path in paths
        ]

    async def fake_index_paper(self: CatenaLibrary, paper_id: int) -> None:
        with Session(self.engine) as session:
            paper = session.get(Paper, paper_id)
            assert paper is not None
            paper.index_status = Status.INDEXED
            session.add(paper)
            session.commit()

    monkeypatch.setattr("catena.library.parse_pdfs", fake_parse_pdfs)
    monkeypatch.setattr(CatenaLibrary, "index_paper", fake_index_paper)

    papers = await library.add_pdfs([first, second], table_id=table_id)

    with Session(library.engine) as session:
        chunks = session.exec(select(PaperChunk)).all()
        memberships = session.exec(select(TablePaper)).all()

    assert len(papers) == 2
    assert len(parse_calls) == 1
    assert len(parse_calls[0]) == 2
    assert len(chunks) == 2
    assert len(memberships) == 2


@pytest.mark.asyncio
async def test_ingest_papers_marks_running_and_emits_progress(monkeypatch, tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    table_id = library.default_table_id()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    registered = library.register_pdfs([first, second], table_id=table_id)
    running_statuses: list[list[str]] = []

    def fake_parse_pdfs(paths: list[Path]) -> list[ParsedPdfResult]:
        with Session(library.engine) as session:
            papers = session.exec(select(Paper).order_by(Paper.id)).all()
            running_statuses.append([paper.parse_status for paper in papers])
        return [
            ParsedPdfResult(
                path=path,
                document=ParsedDocument(
                    markdown=f"# {path.stem}",
                    docling_json={"name": path.name},
                    chunks=[
                        ParsedChunk(
                            index=0,
                            text=f"chunk for {path.name}",
                            page_start=1,
                            page_end=1,
                            heading=None,
                            metadata={},
                        )
                    ],
                ),
            )
            for path in paths
        ]

    async def fake_index_paper(self: CatenaLibrary, paper_id: int) -> None:
        with Session(self.engine) as session:
            paper = session.get(Paper, paper_id)
            assert paper is not None
            paper.index_status = Status.INDEXED
            session.add(paper)
            session.commit()

    monkeypatch.setattr("catena.library.parse_pdfs", fake_parse_pdfs)
    monkeypatch.setattr(CatenaLibrary, "index_paper", fake_index_paper)
    events = []

    results = await library.ingest_papers(
        paper_ids=[item.paper_id for item in registered],
        progress=events.append,
    )

    assert [result.index_status for result in results] == [Status.INDEXED, Status.INDEXED]
    assert running_statuses == [[Status.RUNNING, Status.RUNNING]]
    steps = [event.step for event in events]
    assert steps[:3] == ["queued", "parse", "parse"]
    assert "index" in steps
    assert steps[-1] == "complete"


def test_add_column_queues_cell_for_existing_table_paper(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()
    table_id = library.default_table_id()

    with Session(library.engine) as session:
        paper = Paper(title="A paper", source_path="paper.pdf", parse_status=Status.PARSED)
        session.add(paper)
        session.commit()
        session.refresh(paper)
        paper_id = paper.id

    assert paper_id is not None
    library.add_paper_to_table(table_id, paper_id)
    column = library.add_column(
        "Sample size",
        "What is the total sample size?",
        table_id=table_id,
    )

    with Session(library.engine) as session:
        cells = session.exec(select(ExtractionCell)).all()
        memberships = session.exec(select(TablePaper)).all()

    assert column.id is not None
    assert len(memberships) == 1
    assert len(cells) == 1
    assert cells[0].table_id == table_id
    assert cells[0].paper_id == paper_id
    assert cells[0].status == Status.QUEUED


def test_add_paper_to_second_table_reuses_global_paper_and_queues_only_that_table(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()
    default_table_id = library.default_table_id()
    second_table = library.create_table("Second")
    assert second_table.id is not None

    with Session(library.engine) as session:
        paper = Paper(title="A paper", source_path="paper.pdf", parse_status=Status.PARSED)
        session.add(paper)
        session.commit()
        session.refresh(paper)
        paper_id = paper.id

    assert paper_id is not None
    default_column = library.add_column("Default col", "Default prompt", table_id=default_table_id)
    second_column = library.add_column("Second col", "Second prompt", table_id=second_table.id)

    library.add_paper_to_table(second_table.id, paper_id)

    with Session(library.engine) as session:
        papers = session.exec(select(Paper)).all()
        cells = session.exec(select(ExtractionCell)).all()

    assert default_column.id is not None
    assert second_column.id is not None
    assert len(papers) == 1
    assert len(cells) == 1
    assert cells[0].table_id == second_table.id
    assert cells[0].column_id == second_column.id


@pytest.mark.asyncio
async def test_run_pending_respects_cell_concurrency_and_result_order(monkeypatch, tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path, cell_concurrency=2))
    library.init()
    table_id = library.default_table_id()

    with Session(library.engine) as session:
        papers = [
            Paper(title=f"Paper {index}", source_path=f"paper-{index}.pdf")
            for index in range(3)
        ]
        session.add_all(papers)
        session.commit()
        for paper in papers:
            session.refresh(paper)
        paper_ids = [paper.id for paper in papers]

    assert all(paper_id is not None for paper_id in paper_ids)
    for paper_id in paper_ids:
        library.add_paper_to_table(table_id, paper_id or 0)
    library.add_column("Sample size", "What is the total sample size?", table_id=table_id)

    with Session(library.engine) as session:
        queued_ids = [
            cell.id
            for cell in session.exec(select(ExtractionCell).order_by(ExtractionCell.created_at))
        ]

    assert all(cell_id is not None for cell_id in queued_ids)
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_extract_cell(self, session: Session, cell_id: int) -> ExtractionCell:
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.03 - (0.005 * cell_id))
        async with lock:
            active -= 1
        cell = session.get(ExtractionCell, cell_id)
        assert cell is not None
        return cell

    monkeypatch.setattr("catena.library.ExtractionService.extract_cell", fake_extract_cell)

    results = await library.run_pending()

    assert max_active == 2
    assert [cell.id for cell in results] == queued_ids


def test_compute_similarities_from_existing_chunk_embeddings(tmp_path):
    settings = Settings(data_dir=tmp_path)
    library = CatenaLibrary(settings)
    library.init()

    with Session(library.engine, expire_on_commit=False) as session:
        paper_a = Paper(
            title="Matching paper A",
            source_path="a.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        paper_b = Paper(
            title="Matching paper B",
            source_path="b.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        paper_c = Paper(
            title="Different paper C",
            source_path="c.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        session.add_all([paper_a, paper_b, paper_c])
        session.commit()
        session.refresh(paper_a)
        session.refresh(paper_b)
        session.refresh(paper_c)
        chunks = [
            PaperChunk(
                paper_id=paper_a.id or 0,
                chunk_index=0,
                text="alpha",
                embedding_model="test-embedding",
                embedding_hash="hash-v1",
            ),
            PaperChunk(
                paper_id=paper_b.id or 0,
                chunk_index=0,
                text="alpha copy",
                embedding_model="test-embedding",
                embedding_hash="hash-v1",
            ),
            PaperChunk(
                paper_id=paper_c.id or 0,
                chunk_index=0,
                text="orthogonal",
                embedding_model="test-embedding",
                embedding_hash="hash-v1",
            ),
        ]
        session.add_all(chunks)
        session.commit()
        for chunk in chunks:
            session.refresh(chunk)
        paper_a_id = paper_a.id
        paper_b_id = paper_b.id
        paper_c_id = paper_c.id

    assert paper_a_id is not None
    assert paper_b_id is not None
    assert paper_c_id is not None
    LanceIndex(settings).upsert_chunks(chunks, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])

    results = library.compute_similarities()

    assert len(results) == 3
    with Session(library.engine) as session:
        rows = session.exec(select(PaperSimilarity)).all()
    scores = {(row.paper_id_a, row.paper_id_b): row.score for row in rows}
    assert scores[(paper_a_id, paper_b_id)] == pytest.approx(1.0)
    assert scores[(paper_a_id, paper_c_id)] == pytest.approx(0.0)
    assert scores[(paper_b_id, paper_c_id)] == pytest.approx(0.0)

    similar = library.similar_papers(paper_a_id)
    assert similar[0].paper.id == paper_b_id
    assert similar[0].similarity.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_ask_fast_mode_uses_global_table_retrieval(monkeypatch, tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        gateway_base_url="http://gateway.test",
        gateway_api_key="key",
        llm_model="llm",
        embedding_model="embedding",
    )
    library = CatenaLibrary(settings)
    library.init()
    table_id = library.default_table_id()

    with Session(library.engine, expire_on_commit=False) as session:
        papers = [
            Paper(title=f"Paper {index}", source_path=f"p{index}.pdf")
            for index in range(2)
        ]
        session.add_all(papers)
        session.commit()
        for paper in papers:
            session.refresh(paper)
        chunks = [
            PaperChunk(
                paper_id=papers[0].id or 0,
                chunk_index=0,
                text="alpha metric evidence",
                page_start=1,
            ),
            PaperChunk(
                paper_id=papers[1].id or 0,
                chunk_index=0,
                text="beta metric evidence",
                page_start=2,
            ),
        ]
        session.add_all(chunks)
        session.commit()
        for chunk in chunks:
            session.refresh(chunk)
        paper_ids = [paper.id for paper in papers]

    assert all(paper_id is not None for paper_id in paper_ids)
    for paper_id in paper_ids:
        library.add_paper_to_table(table_id, paper_id or 0)
    monkeypatch.setattr("catena.qa.embed_query", _fake_embed_query)
    LanceIndex(settings).upsert_chunks(chunks, [[1.0, 0.0], [0.0, 1.0]])

    contexts: list[str] = []

    async def fake_call_baml(
        self: QuestionAnswerService,
        question: str,
        evidence_context: str,
    ) -> dict[str, object]:
        contexts.append(evidence_context)
        return {"answer": "global", "evidence": [], "confidence": "HIGH"}

    monkeypatch.setattr(QuestionAnswerService, "_call_baml", fake_call_baml)

    answer = await library.ask("Which metric?", table_id=table_id, mode="fast", top_k=2)

    assert answer.mode == "fast"
    assert answer.answer == "global"
    assert set(answer.retrieved_chunk_ids) == {chunks[0].id, chunks[1].id}
    assert f"paper_id={paper_ids[0]}" in contexts[0]
    assert f"paper_id={paper_ids[1]}" in contexts[0]


@pytest.mark.asyncio
async def test_ask_matrix_mode_uses_completed_cells(monkeypatch, tmp_path):
    library = CatenaLibrary(
        Settings(
            data_dir=tmp_path,
            gateway_base_url="http://gateway.test",
            gateway_api_key="key",
            llm_model="llm",
            embedding_model="embedding",
        )
    )
    library.init()
    table_id = library.default_table_id()

    with Session(library.engine, expire_on_commit=False) as session:
        paper = Paper(title="Matrix Paper", source_path="paper.pdf")
        session.add(paper)
        session.commit()
        session.refresh(paper)
        assert paper.id is not None
        paper_id = paper.id

    library.add_paper_to_table(table_id, paper_id)
    column = library.add_column("Sample size", "What is the sample size?", table_id=table_id)
    with Session(library.engine, expire_on_commit=False) as session:
        cell = session.exec(
            select(ExtractionCell).where(
                ExtractionCell.table_id == table_id,
                ExtractionCell.paper_id == paper_id,
                ExtractionCell.column_id == column.id,
            )
        ).one()
        cell.status = Status.ANSWERED
        cell.answer_text = "128 participants"
        cell.confidence = "high"
        cell.evidence_json = [
            {"quote": "We enrolled 128 participants.", "page": 4, "chunk_id": "7"}
        ]
        session.add(cell)
        session.commit()

    contexts: list[str] = []

    async def fake_call_baml(
        self: QuestionAnswerService,
        question: str,
        evidence_context: str,
    ) -> dict[str, object]:
        contexts.append(evidence_context)
        return {"answer": "128 participants", "evidence": [], "confidence": "HIGH"}

    monkeypatch.setattr(QuestionAnswerService, "_call_baml", fake_call_baml)

    answer = await library.ask(
        "What sample size is in the table?",
        table_id=table_id,
        mode="matrix",
    )

    assert answer.mode == "matrix"
    assert answer.retrieved_chunk_ids == []
    assert answer.raw["matrix_cell_count"] == 1
    assert "source=extraction_matrix" in contexts[0]
    assert "column=Sample size" in contexts[0]
    assert "answer=128 participants" in contexts[0]


@pytest.mark.asyncio
async def test_ask_auto_uses_synthesis_for_large_tables(monkeypatch, tmp_path):
    library = CatenaLibrary(
        Settings(
            data_dir=tmp_path,
            gateway_base_url="http://gateway.test",
            gateway_api_key="key",
            llm_model="llm",
            embedding_model="embedding",
        )
    )
    library.init()
    table_id = library.default_table_id()
    paper_count = 9

    with Session(library.engine, expire_on_commit=False) as session:
        papers = [
            Paper(title=f"Paper {index}", source_path=f"paper-{index}.pdf")
            for index in range(paper_count)
        ]
        session.add_all(papers)
        session.commit()
        for paper in papers:
            session.refresh(paper)
            assert paper.id is not None
            session.add(
                PaperChunk(
                    paper_id=paper.id,
                    chunk_index=0,
                    text=f"Evidence from {paper.title}",
                    page_start=1,
                )
            )
        session.commit()
        paper_ids = [paper.id for paper in papers]

    for paper_id in paper_ids:
        assert paper_id is not None
        library.add_paper_to_table(table_id, paper_id)

    monkeypatch.setattr("catena.qa.embed_query", _fake_embed_query)

    map_labels: list[str] = []

    async def fake_map(
        self: QuestionAnswerService,
        question: str,
        context_label: str,
        evidence_context: str,
    ) -> dict[str, object]:
        map_labels.append(context_label)
        return {
            "answer": f"{context_label} answer",
            "evidence": [],
            "confidence": "HIGH",
        }

    async def fake_reduce(
        self: QuestionAnswerService,
        question: str,
        intermediate_answers: str,
    ) -> dict[str, object]:
        assert "paper 1 answer" in intermediate_answers
        return {"answer": "synthesized", "evidence": [], "confidence": "HIGH"}

    monkeypatch.setattr(QuestionAnswerService, "_call_baml_map", fake_map)
    monkeypatch.setattr(QuestionAnswerService, "_call_baml_reduce", fake_reduce)

    answer = await library.ask("What is common overall?", table_id=table_id)

    assert answer.mode == "synthesis"
    assert answer.answer == "synthesized"
    assert len(map_labels) == paper_count
    assert answer.raw["intermediate_count"] == paper_count


async def _fake_embed_query(settings: Settings, query: str) -> list[float]:
    return [1.0, 0.0]


def test_tags_and_filter_table_creation_do_not_duplicate_papers(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()

    with Session(library.engine) as session:
        paper_a = Paper(
            title="A",
            source_path="a.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
            year=2024,
            citation_count=15,
            venue="NeurIPS",
        )
        paper_b = Paper(
            title="B",
            source_path="b.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
            year=2020,
            citation_count=1,
            venue="Workshop",
        )
        session.add(paper_a)
        session.add(paper_b)
        session.commit()
        session.refresh(paper_a)
        session.refresh(paper_b)
        paper_a_id = paper_a.id
        paper_b_id = paper_b.id

    assert paper_a_id is not None
    assert paper_b_id is not None
    library.tag_paper(paper_a_id, "hft")
    library.tag_paper(paper_a_id, "fpga")
    library.tag_paper(paper_b_id, "hft")

    paper_filter = PaperFilter(tags_all=["hft"], year_min=2022, citations_min=10)
    table, matched = library.create_table_from_filter("Recent cited HFT", paper_filter)

    with Session(library.engine) as session:
        papers = session.exec(select(Paper)).all()
        memberships = session.exec(select(TablePaper)).all()

    assert table.id is not None
    assert [paper.id for paper in matched] == [paper_a_id]
    assert len(papers) == 2
    assert len(memberships) == 1
    assert memberships[0].paper_id == paper_a_id


@pytest.mark.asyncio
async def test_search_supports_title_text_exact_and_table_scope(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()
    table = library.create_table("Neural papers")

    with Session(library.engine, expire_on_commit=False) as session:
        paper_a = Paper(
            title="Graph Neural Networks for Molecules",
            source_path="a.pdf",
            abstract="Molecular property prediction with message passing.",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        paper_b = Paper(
            title="Kernel Methods",
            source_path="b.pdf",
            abstract="Classical models.",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        session.add_all([paper_a, paper_b])
        session.commit()
        session.refresh(paper_a)
        session.refresh(paper_b)
        assert paper_a.id is not None
        assert paper_b.id is not None
        session.add_all(
            [
                PaperChunk(
                    paper_id=paper_a.id,
                    chunk_index=0,
                    text="The model uses message passing neural updates.",
                    page_start=3,
                    heading="Method",
                ),
                PaperChunk(
                    paper_id=paper_b.id,
                    chunk_index=0,
                    text="The appendix discusses support vector machines.",
                    page_start=9,
                    heading="Appendix",
                ),
            ]
        )
        session.commit()
        paper_a_id = paper_a.id
        paper_b_id = paper_b.id

    library.add_paper_to_table(table.id or 0, paper_a_id)
    library.rebuild_search_index()

    title_hits = await library.search("graph neural", mode="title", top_k=5)
    assert [hit.paper_id for hit in title_hits] == [paper_a_id]

    text_hits = await library.search("message passing", mode="text", top_k=5)
    assert text_hits[0].paper_id == paper_a_id
    assert text_hits[0].chunk_id is not None

    exact_hits = await library.search("support vector machines", mode="exact", top_k=5)
    assert exact_hits[0].paper_id == paper_b_id
    assert exact_hits[0].kind == "exact_text"

    scoped_hits = await library.search(
        "support vector machines",
        mode="exact",
        table_id=table.id,
        top_k=5,
    )
    assert scoped_hits == []


@pytest.mark.asyncio
async def test_search_hybrid_component_scores_keep_per_source_values(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()

    with Session(library.engine, expire_on_commit=False) as session:
        paper = Paper(
            title="Graph Neural Networks for Molecules",
            source_path="a.pdf",
            abstract="Molecular property prediction with message passing.",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        session.add(paper)
        session.commit()
        session.refresh(paper)
        assert paper.id is not None
        session.add(
            PaperChunk(
                paper_id=paper.id,
                chunk_index=0,
                text="The model uses message passing neural updates.",
                page_start=3,
                heading="Method",
            )
        )
        session.commit()
        paper_id = paper.id

    library.rebuild_search_index()

    hits = await library.search("neural", mode="hybrid", top_k=5)
    assert hits, "expected at least one hybrid hit"
    assert any(hit.paper_id == paper_id for hit in hits)

    # "neural" matches both the title (paper-level key) and the chunk text
    # (chunk-level key), so each fused hit should carry the true per-source
    # score for every list that touched it. Previously the RRF score reset
    # zeroed the originating source's contribution, leaving entries like
    # {"title": 0.0, "exact": 2.0}.
    for hit in hits:
        assert hit.component_scores, f"hit p{hit.paper_id} has no component scores"
        assert all(
            value > 0.0 for value in hit.component_scores.values()
        ), f"zeroed component score on {hit.kind}: {hit.component_scores}"
        assert hit.score > 0.0

    sources = {name for hit in hits for name in hit.component_scores}
    assert "title" in sources
    assert "text" in sources
