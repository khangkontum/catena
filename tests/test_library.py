import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import Session, select

from catena.config import Settings
from catena.filters import PaperFilter
from catena.library import CatenaLibrary
from catena.models import ExtractionCell, Paper, PaperChunk, PaperSimilarity, Status, TablePaper
from catena.parsing import ParsedChunk, ParsedDocument, ParsedPdfResult
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
