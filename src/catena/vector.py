from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from catena.config import Settings
from catena.models import PaperChunk

CHUNKS_TABLE = "chunks"


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    paper_id: int
    text: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    score: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ChunkVector:
    chunk_id: int
    paper_id: int
    vector: list[float]
    embedding_model: str | None
    embedding_hash: str | None


class LanceIndex:
    """Rebuildable LanceDB index for parsed paper chunks."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def upsert_chunks(self, chunks: list[PaperChunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError(f"Got {len(chunks)} chunks but {len(vectors)} vectors")
        db = self._connect()
        rows = [self._row(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
        table_names = _table_names(db)
        if CHUNKS_TABLE not in table_names:
            db.create_table(CHUNKS_TABLE, data=rows)
            return
        table = db.open_table(CHUNKS_TABLE)
        paper_ids = sorted({chunk.paper_id for chunk in chunks})
        for paper_id in paper_ids:
            table.delete(f"paper_id = {paper_id}")
        table.add(rows)

    def search(self, query_vector: list[float], *, paper_id: int, limit: int) -> list[SearchHit]:
        db = self._connect()
        if CHUNKS_TABLE not in _table_names(db):
            return []
        table = db.open_table(CHUNKS_TABLE)
        rows = (
            table.search(query_vector)
            .where(f"paper_id = {paper_id}")
            .limit(max(1, limit))
            .to_list()
        )
        return [_hit_from_row(row) for row in rows]

    def chunk_vectors(self, *, paper_ids: set[int] | None = None) -> list[ChunkVector]:
        """Return persisted chunk embeddings from the rebuildable LanceDB index."""

        db = self._connect()
        if CHUNKS_TABLE not in _table_names(db):
            return []
        table = db.open_table(CHUNKS_TABLE)
        rows = table.to_arrow().to_pylist()
        vectors: list[ChunkVector] = []
        for row in rows:
            paper_id = _optional_int(row.get("paper_id"))
            chunk_id = _optional_int(row.get("chunk_id"))
            raw_vector = row.get("vector")
            if paper_id is None or chunk_id is None or not isinstance(raw_vector, list):
                continue
            if paper_ids is not None and paper_id not in paper_ids:
                continue
            vectors.append(
                ChunkVector(
                    chunk_id=chunk_id,
                    paper_id=paper_id,
                    vector=[float(value) for value in raw_vector],
                    embedding_model=_optional_str(row.get("embedding_model")),
                    embedding_hash=_optional_str(row.get("embedding_hash")),
                )
            )
        return vectors

    def delete_paper(self, paper_id: int) -> None:
        db = self._connect()
        if CHUNKS_TABLE not in _table_names(db):
            return
        db.open_table(CHUNKS_TABLE).delete(f"paper_id = {paper_id}")

    def _connect(self) -> Any:
        import lancedb

        self.settings.ensure_dirs()
        return lancedb.connect(str(self.settings.lancedb_uri))

    @staticmethod
    def _row(chunk: PaperChunk, vector: list[float]) -> dict[str, Any]:
        if chunk.id is None:
            raise ValueError("Cannot index an unsaved PaperChunk without an id")
        return {
            "chunk_id": chunk.id,
            "paper_id": chunk.paper_id,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "heading": chunk.heading,
            "embedding_model": chunk.embedding_model,
            "parser_hash": chunk.parser_hash,
            "embedding_hash": chunk.embedding_hash,
            "vector": vector,
        }


def _table_names(db: Any) -> set[str]:
    if hasattr(db, "list_tables"):
        tables = db.list_tables()
        return set(getattr(tables, "tables", tables))
    return set(db.table_names())


def _hit_from_row(row: dict[str, Any]) -> SearchHit:
    score = row.get("_distance")
    return SearchHit(
        chunk_id=int(row["chunk_id"]),
        paper_id=int(row["paper_id"]),
        text=str(row.get("text") or ""),
        page_start=_optional_int(row.get("page_start")),
        page_end=_optional_int(row.get("page_end")),
        heading=row.get("heading"),
        score=float(score) if score is not None else None,
        raw=row,
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
