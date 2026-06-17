from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import or_
from sqlmodel import Session, select

from catena.config import Settings
from catena.models import Paper, PaperSimilarity, TablePaper, utcnow
from catena.vector import ChunkVector, LanceIndex

SIMILARITY_ALGORITHM = "paper-centroid-cosine-v1"


@dataclass(frozen=True)
class PaperVector:
    paper_id: int
    vector: list[float]
    chunk_count: int
    embedding_model: str | None
    embedding_hash: str | None
    embedding_models: list[str]
    embedding_hashes: list[str]


@dataclass(frozen=True)
class SimilarPaper:
    paper: Paper
    similarity: PaperSimilarity


class SimilarityService:
    """Compute and query local paper-pair similarity scores.

    The initial algorithm is intentionally local and deterministic: it builds one centroid
    vector per paper from already-indexed chunk embeddings and compares paper centroids with
    cosine similarity. No LLM call or external recommendation API is used.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.index = LanceIndex(settings)

    def compute(
        self,
        session: Session,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
    ) -> list[PaperSimilarity]:
        resolved_ids = _resolve_paper_ids(session, paper_ids=paper_ids, table_id=table_id)
        if len(resolved_ids) < 2:
            return []

        paper_vectors = self.paper_vectors(set(resolved_ids))
        existing = _existing_rows(session, set(resolved_ids))
        results: list[PaperSimilarity] = []
        for paper_id_a, paper_id_b in combinations(resolved_ids, 2):
            vector_a = paper_vectors.get(paper_id_a)
            vector_b = paper_vectors.get(paper_id_b)
            if vector_a is None or vector_b is None:
                continue
            cosine = cosine_similarity(vector_a.vector, vector_b.vector)
            score = _score_from_cosine(cosine)
            key = _ordered_pair(paper_id_a, paper_id_b)
            row = existing.get(key)
            now = utcnow()
            if row is None:
                row = PaperSimilarity(
                    paper_id_a=key[0],
                    paper_id_b=key[1],
                    score=score,
                    cosine_similarity=cosine,
                    algorithm=SIMILARITY_ALGORITHM,
                    created_at=now,
                )
            row.score = score
            row.cosine_similarity = cosine
            row.algorithm = SIMILARITY_ALGORITHM
            row.embedding_model = _shared_value(vector_a.embedding_model, vector_b.embedding_model)
            row.embedding_hash = _shared_value(vector_a.embedding_hash, vector_b.embedding_hash)
            row.details_json = _details(vector_a, vector_b)
            row.updated_at = now
            session.add(row)
            session.flush()
            existing[key] = row
            results.append(row)
        return sorted(results, key=lambda item: item.score, reverse=True)

    def paper_vectors(self, paper_ids: set[int] | None = None) -> dict[int, PaperVector]:
        chunk_vectors = self.index.chunk_vectors(paper_ids=paper_ids)
        return _paper_vectors_from_chunks(chunk_vectors)

    @staticmethod
    def similar_papers(
        session: Session,
        paper_id: int,
        *,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SimilarPaper]:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise ValueError(f"Paper {paper_id} not found")

        statement = select(PaperSimilarity).where(
            or_(PaperSimilarity.paper_id_a == paper_id, PaperSimilarity.paper_id_b == paper_id)
        )
        if min_score is not None:
            statement = statement.where(PaperSimilarity.score >= min_score)
        statement = statement.order_by(PaperSimilarity.score.desc()).limit(max(1, limit))
        rows = list(session.exec(statement).all())
        other_ids = [
            row.paper_id_b if row.paper_id_a == paper_id else row.paper_id_a for row in rows
        ]
        if not other_ids:
            return []
        papers = session.exec(select(Paper).where(Paper.id.in_(other_ids))).all()  # type: ignore[union-attr]
        papers_by_id = {paper.id: paper for paper in papers}
        return [
            SimilarPaper(paper=papers_by_id[other_id], similarity=row)
            for row, other_id in zip(rows, other_ids, strict=True)
            if other_id in papers_by_id
        ]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"Cannot compare vectors with dimensions {len(left)} and {len(right)}")
    if not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _resolve_paper_ids(
    session: Session,
    *,
    paper_ids: list[int] | None,
    table_id: int | None,
) -> list[int]:
    if paper_ids:
        resolved_ids = _unique_sorted(paper_ids)
        papers = session.exec(select(Paper).where(Paper.id.in_(resolved_ids))).all()  # type: ignore[union-attr]
        found_ids = {paper.id for paper in papers}
        missing = [paper_id for paper_id in resolved_ids if paper_id not in found_ids]
        if missing:
            raise ValueError(f"Paper id(s) not found: {', '.join(str(item) for item in missing)}")
        return resolved_ids
    if table_id is not None:
        memberships = session.exec(
            select(TablePaper)
            .where(TablePaper.table_id == table_id)
            .order_by(TablePaper.created_at)
        ).all()
        return _unique_sorted([membership.paper_id for membership in memberships])
    papers = session.exec(select(Paper).order_by(Paper.id)).all()
    return [paper.id for paper in papers if paper.id is not None]


def _existing_rows(session: Session, paper_ids: set[int]) -> dict[tuple[int, int], PaperSimilarity]:
    if not paper_ids:
        return {}
    rows = session.exec(
        select(PaperSimilarity).where(
            or_(
                PaperSimilarity.paper_id_a.in_(paper_ids),  # type: ignore[attr-defined]
                PaperSimilarity.paper_id_b.in_(paper_ids),  # type: ignore[attr-defined]
            )
        )
    ).all()
    return {(_ordered_pair(row.paper_id_a, row.paper_id_b)): row for row in rows}


def _paper_vectors_from_chunks(chunk_vectors: list[ChunkVector]) -> dict[int, PaperVector]:
    grouped: dict[int, list[ChunkVector]] = defaultdict(list)
    for chunk_vector in chunk_vectors:
        grouped[chunk_vector.paper_id].append(chunk_vector)

    paper_vectors: dict[int, PaperVector] = {}
    for paper_id, chunks in grouped.items():
        normalized_chunk_vectors = [_normalize(chunk.vector) for chunk in chunks]
        normalized_chunk_vectors = [vector for vector in normalized_chunk_vectors if vector]
        if not normalized_chunk_vectors:
            continue
        dimensions = {len(vector) for vector in normalized_chunk_vectors}
        if len(dimensions) != 1:
            raise ValueError(f"Paper {paper_id} has chunk embeddings with multiple dimensions")
        centroid = _normalize(_mean_vector(normalized_chunk_vectors))
        if not centroid:
            continue
        embedding_models = sorted(
            {chunk.embedding_model for chunk in chunks if chunk.embedding_model is not None}
        )
        embedding_hashes = sorted(
            {chunk.embedding_hash for chunk in chunks if chunk.embedding_hash is not None}
        )
        paper_vectors[paper_id] = PaperVector(
            paper_id=paper_id,
            vector=centroid,
            chunk_count=len(normalized_chunk_vectors),
            embedding_model=embedding_models[0] if len(embedding_models) == 1 else None,
            embedding_hash=embedding_hashes[0] if len(embedding_hashes) == 1 else None,
            embedding_models=embedding_models,
            embedding_hashes=embedding_hashes,
        )
    return paper_vectors


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return []
    return [value / norm for value in vector]


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dimension = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)]


def _score_from_cosine(value: float) -> float:
    return max(0.0, min(1.0, value))


def _details(vector_a: PaperVector, vector_b: PaperVector) -> dict[str, object]:
    return {
        "algorithm": SIMILARITY_ALGORITHM,
        "score_scale": "clamped cosine similarity, 0.0 to 1.0; higher is more similar",
        "vector_aggregation": (
            "normalize each chunk vector, average chunks per paper, normalize centroid"
        ),
        "paper_a": {
            "paper_id": vector_a.paper_id,
            "chunk_count": vector_a.chunk_count,
            "embedding_models": vector_a.embedding_models,
            "embedding_hashes": vector_a.embedding_hashes,
        },
        "paper_b": {
            "paper_id": vector_b.paper_id,
            "chunk_count": vector_b.chunk_count,
            "embedding_models": vector_b.embedding_models,
            "embedding_hashes": vector_b.embedding_hashes,
        },
    }


def _shared_value(left: str | None, right: str | None) -> str | None:
    return left if left and left == right else None


def _ordered_pair(paper_id_a: int, paper_id_b: int) -> tuple[int, int]:
    return (paper_id_a, paper_id_b) if paper_id_a < paper_id_b else (paper_id_b, paper_id_a)


def _unique_sorted(values: list[int]) -> list[int]:
    return sorted(set(values))
