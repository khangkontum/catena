from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlmodel import Session, select

from catena.config import Settings
from catena.embeddings import embed_query
from catena.models import Paper, PaperChunk, TablePaper
from catena.vector import LanceIndex, SearchHit

SearchMode = Literal["auto", "hybrid", "semantic", "text", "title", "exact"]

SEARCH_FTS_TABLE = "paper_search_fts"
_RRF_K = 60
_VALID_MODES: set[str] = {"auto", "hybrid", "semantic", "text", "title", "exact"}


@dataclass(frozen=True)
class SearchResult:
    paper_id: int
    paper_title: str
    score: float
    kind: str
    snippet: str
    chunk_id: int | None = None
    chunk_index: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    heading: str | None = None
    component_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class _Candidate:
    paper_id: int
    paper_title: str
    kind: str
    snippet: str
    chunk_id: int | None = None
    chunk_index: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    heading: str | None = None
    score: float = 0.0
    component_scores: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int]:
        if self.chunk_id is not None:
            return ("chunk", self.chunk_id)
        return ("paper", self.paper_id)


class SearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.index = LanceIndex(settings)

    async def search(
        self,
        session: Session,
        query: str,
        *,
        mode: SearchMode = "auto",
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        top_k: int | None = None,
    ) -> list[SearchResult]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("Search query cannot be empty")
        if mode not in _VALID_MODES:
            raise ValueError(f"Search mode must be one of: {', '.join(sorted(_VALID_MODES))}")

        limit = max(1, top_k or self.settings.top_k)
        scoped_ids = _resolve_paper_ids(session, paper_ids=paper_ids, table_id=table_id)
        papers_by_id = _papers_by_id(session, scoped_ids)
        if scoped_ids and len(papers_by_id) != len(scoped_ids):
            missing = [paper_id for paper_id in scoped_ids if paper_id not in papers_by_id]
            raise ValueError(f"Paper id(s) not found: {', '.join(str(item) for item in missing)}")
        if not scoped_ids:
            scoped_ids = sorted(papers_by_id)

        ranked_lists: dict[str, list[_Candidate]] = {}
        if mode in {"auto", "hybrid", "title"}:
            ranked_lists["title"] = _title_candidates(session, cleaned_query, scoped_ids, limit)
        if mode in {"auto", "hybrid", "text"}:
            ranked_lists["text"] = _fts_candidates(session, cleaned_query, scoped_ids, limit)
        if mode in {"auto", "hybrid", "exact"}:
            ranked_lists["exact"] = _exact_candidates(session, cleaned_query, scoped_ids, limit)
        if mode in {"auto", "hybrid", "semantic"} and (
            mode == "semantic" or self.settings.gateway_ready
        ):
            ranked_lists["semantic"] = await self._semantic_candidates(
                cleaned_query,
                scoped_ids,
                limit,
                papers_by_id=papers_by_id,
                session=session,
            )

        candidates = _fuse_ranked_lists(ranked_lists, limit)
        return [
            SearchResult(
                paper_id=candidate.paper_id,
                paper_title=candidate.paper_title,
                score=candidate.score,
                kind=candidate.kind,
                snippet=candidate.snippet,
                chunk_id=candidate.chunk_id,
                chunk_index=candidate.chunk_index,
                page_start=candidate.page_start,
                page_end=candidate.page_end,
                heading=candidate.heading,
                component_scores=candidate.component_scores,
            )
            for candidate in candidates
        ]

    async def _semantic_candidates(
        self,
        query: str,
        paper_ids: list[int],
        limit: int,
        *,
        papers_by_id: dict[int, Paper],
        session: Session,
    ) -> list[_Candidate]:
        self.settings.require_gateway()
        vector = await embed_query(self.settings, query)
        hits = self.index.search_many(vector, paper_ids=set(paper_ids) or None, limit=limit)
        if not hits:
            return []
        missing_papers = {hit.paper_id for hit in hits if hit.paper_id not in papers_by_id}
        if missing_papers:
            papers_by_id.update(_papers_by_id(session, sorted(missing_papers)))
        return [
            _candidate_from_hit(hit, papers_by_id[hit.paper_id], "semantic")
            for hit in hits
            if hit.paper_id in papers_by_id
        ]


def rebuild_search_index(session: Session, *, paper_id: int | None = None) -> None:
    """Rebuild local FTS rows for all papers or one paper."""

    connection = session.connection()
    if paper_id is None:
        connection.execute(text(f"DELETE FROM {SEARCH_FTS_TABLE}"))
        where_clause = ""
        params: dict[str, Any] = {}
    else:
        connection.execute(
            text(f"DELETE FROM {SEARCH_FTS_TABLE} WHERE paper_id = :paper_id"),
            {"paper_id": paper_id},
        )
        where_clause = "WHERE c.paper_id = :paper_id"
        params = {"paper_id": paper_id}

    connection.execute(
        text(
            f"""
            INSERT INTO {SEARCH_FTS_TABLE} (
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
            {where_clause}
            """
        ),
        params,
    )


def _resolve_paper_ids(
    session: Session,
    *,
    paper_ids: list[int] | None,
    table_id: int | None,
) -> list[int]:
    explicit_ids = _unique_ints(paper_ids or [])
    if explicit_ids:
        return explicit_ids
    if table_id is None:
        papers = session.exec(select(Paper.id).order_by(Paper.id)).all()
        return [paper_id for paper_id in papers if paper_id is not None]
    memberships = session.exec(
        select(TablePaper).where(TablePaper.table_id == table_id).order_by(TablePaper.created_at)
    ).all()
    return [membership.paper_id for membership in memberships]


def _papers_by_id(session: Session, paper_ids: list[int]) -> dict[int, Paper]:
    if paper_ids:
        papers = session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()  # type: ignore[union-attr]
    else:
        papers = session.exec(select(Paper).order_by(Paper.id)).all()
    return {paper.id or 0: paper for paper in papers if paper.id is not None}


def _title_candidates(
    session: Session,
    query: str,
    paper_ids: list[int],
    limit: int,
) -> list[_Candidate]:
    needle = query.casefold()
    candidates: list[_Candidate] = []
    for paper in _scoped_papers(session, paper_ids):
        title = paper.title or ""
        folded = title.casefold()
        if needle not in folded:
            continue
        starts = folded.startswith(needle)
        exact = folded == needle
        score = 3.0 if exact else 2.0 if starts else 1.0
        candidates.append(
            _Candidate(
                paper_id=paper.id or 0,
                paper_title=title,
                kind="title",
                snippet=title,
                score=score,
                component_scores={"title": score},
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.paper_title.casefold()))
    return candidates[:limit]


def _fts_candidates(
    session: Session,
    query: str,
    paper_ids: list[int],
    limit: int,
) -> list[_Candidate]:
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    scope_sql, params = _scope_sql(paper_ids)
    params.update({"query": fts_query, "limit": limit})
    try:
        rows = session.connection().execute(
            text(
                f"""
                SELECT
                    f.chunk_id,
                    f.paper_id,
                    p.title AS paper_title,
                    c.chunk_index,
                    f.page_start,
                    f.page_end,
                    f.heading,
                    snippet({SEARCH_FTS_TABLE}, 8, '[', ']', ' … ', 28) AS snippet,
                    bm25(
                        {SEARCH_FTS_TABLE},
                        0.0, 0.0, 6.0, 2.0, 5.0, 1.0, 1.0, 2.0, 1.0, 0.0, 0.0
                    ) AS rank
                FROM {SEARCH_FTS_TABLE} f
                JOIN papers p ON p.id = f.paper_id
                JOIN paper_chunks c ON c.id = f.chunk_id
                WHERE {SEARCH_FTS_TABLE} MATCH :query
                {scope_sql}
                ORDER BY rank
                LIMIT :limit
                """
            ),
            params,
        ).mappings()
    except Exception:
        return []

    candidates: list[_Candidate] = []
    for row in rows:
        rank = float(row["rank"])
        score = 1.0 / (1.0 + max(rank, 0.0))
        candidates.append(
            _Candidate(
                paper_id=int(row["paper_id"]),
                paper_title=str(row["paper_title"] or ""),
                chunk_id=int(row["chunk_id"]),
                chunk_index=_optional_int(row["chunk_index"]),
                page_start=_optional_int(row["page_start"]),
                page_end=_optional_int(row["page_end"]),
                heading=_optional_str(row["heading"]),
                kind="text",
                snippet=str(row["snippet"] or ""),
                score=score,
                component_scores={"text": score},
            )
        )
    return candidates


def _exact_candidates(
    session: Session,
    query: str,
    paper_ids: list[int],
    limit: int,
) -> list[_Candidate]:
    needle = query.casefold()
    candidates: list[_Candidate] = []
    for paper in _scoped_papers(session, paper_ids):
        if needle in (paper.title or "").casefold():
            title = paper.title or ""
            candidates.append(
                _Candidate(
                    paper_id=paper.id or 0,
                    paper_title=title,
                    kind="exact_title",
                    snippet=title,
                    score=2.0,
                    component_scores={"exact": 2.0},
                )
            )
        abstract = paper.abstract or ""
        if needle in abstract.casefold():
            candidates.append(
                _Candidate(
                    paper_id=paper.id or 0,
                    paper_title=paper.title,
                    kind="exact_abstract",
                    snippet=_literal_snippet(abstract, query),
                    score=1.5,
                    component_scores={"exact": 1.5},
                )
            )

    statement = select(PaperChunk, Paper).join(Paper, Paper.id == PaperChunk.paper_id)
    if paper_ids:
        statement = statement.where(PaperChunk.paper_id.in_(paper_ids))  # type: ignore[union-attr]
    chunks = session.exec(statement.order_by(PaperChunk.paper_id, PaperChunk.chunk_index)).all()
    for chunk, paper in chunks:
        if needle not in chunk.text.casefold():
            continue
        candidates.append(
            _Candidate(
                paper_id=paper.id or 0,
                paper_title=paper.title,
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                heading=chunk.heading,
                kind="exact_text",
                snippet=_literal_snippet(chunk.text, query),
                score=1.0,
                component_scores={"exact": 1.0},
            )
        )
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def _scoped_papers(session: Session, paper_ids: list[int]) -> list[Paper]:
    statement = select(Paper).order_by(Paper.id)
    if paper_ids:
        statement = statement.where(Paper.id.in_(paper_ids))  # type: ignore[union-attr]
    return list(session.exec(statement).all())


def _candidate_from_hit(hit: SearchHit, paper: Paper, kind: str) -> _Candidate:
    distance = hit.score if hit.score is not None else 0.0
    score = 1.0 / (1.0 + max(distance, 0.0))
    return _Candidate(
        paper_id=hit.paper_id,
        paper_title=paper.title,
        chunk_id=hit.chunk_id,
        page_start=hit.page_start,
        page_end=hit.page_end,
        heading=hit.heading,
        kind=kind,
        snippet=_collapse(hit.text)[:320],
        score=score,
        component_scores={kind: score},
    )


def _fuse_ranked_lists(
    ranked_lists: dict[str, list[_Candidate]],
    limit: int,
) -> list[_Candidate]:
    by_key: dict[tuple[str, int], _Candidate] = {}
    for source, candidates in ranked_lists.items():
        for rank, candidate in enumerate(candidates, start=1):
            fused = by_key.get(candidate.key)
            contribution = 1.0 / (_RRF_K + rank)
            if fused is None:
                fused = candidate
                fused.score = 0.0
                fused.component_scores = {}
                by_key[candidate.key] = fused
            if candidate.score > fused.component_scores.get(source, -1.0):
                fused.component_scores[source] = candidate.score
            fused.score += contribution
            if _kind_priority(candidate.kind) < _kind_priority(fused.kind):
                fused.kind = candidate.kind
                fused.snippet = candidate.snippet
    results = sorted(
        by_key.values(),
        key=lambda item: (-item.score, item.paper_id, item.chunk_id or 0),
    )
    return results[:limit]


def _kind_priority(kind: str) -> int:
    if kind == "exact_title":
        return 0
    if kind == "title":
        return 1
    if kind == "exact_text":
        return 2
    if kind == "text":
        return 3
    if kind == "semantic":
        return 4
    return 5


def _scope_sql(paper_ids: list[int]) -> tuple[str, dict[str, Any]]:
    if not paper_ids:
        return "", {}
    placeholders = ", ".join(f":paper_id_{index}" for index, _ in enumerate(paper_ids))
    params = {f"paper_id_{index}": paper_id for index, paper_id in enumerate(paper_ids)}
    return f"AND f.paper_id IN ({placeholders})", params


def _fts_query(query: str) -> str:
    quoted_phrases = re.findall(r'"([^"]+)"', query)
    tokens = re.findall(r"[\w-]+", query)
    parts = [_quote_fts(phrase) for phrase in quoted_phrases if phrase.strip()]
    parts.extend(_quote_fts(token) for token in tokens if token.strip())
    return " OR ".join(dict.fromkeys(parts))


def _quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal_snippet(text_value: str, query: str, *, radius: int = 140) -> str:
    folded = text_value.casefold()
    needle = query.casefold()
    index = folded.find(needle)
    if index < 0:
        return _collapse(text_value)[: radius * 2]
    start = max(0, index - radius)
    end = min(len(text_value), index + len(query) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text_value) else ""
    return f"{prefix}{_collapse(text_value[start:end])}{suffix}"


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


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
    text_value = str(value)
    return text_value or None
