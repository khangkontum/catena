from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from baml_py import ClientRegistry
from sqlmodel import Session, select

from catena.config import Settings
from catena.embeddings import embed_query
from catena.models import Paper, PaperChunk, TablePaper
from catena.vector import LanceIndex, SearchHit

MAX_QA_CONTEXT_CHARS = 32_000


@dataclass(frozen=True)
class OneOffAnswer:
    question: str
    paper_ids: list[int]
    answer: str
    evidence: list[dict[str, Any]]
    confidence: str | None
    rationale: str | None
    raw: dict[str, Any]
    retrieved_chunk_ids: list[int]


@dataclass(frozen=True)
class PaperHit:
    paper: Paper
    hit: SearchHit


class QuestionAnswerService:
    """One-off retrieval and answer service.

    This intentionally has no chat/session/history table. Each call retrieves context from
    the requested papers and sends only that context plus the current question to BAML.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.index = LanceIndex(settings)

    async def ask(
        self,
        session: Session,
        question: str,
        *,
        paper_ids: list[int] | None = None,
        table_id: int | None = None,
        top_k: int | None = None,
    ) -> OneOffAnswer:
        cleaned_question = question.strip()
        if not cleaned_question:
            raise ValueError("Question cannot be empty")
        resolved_paper_ids = self._resolve_paper_ids(
            session,
            paper_ids=paper_ids,
            table_id=table_id,
        )
        if not resolved_paper_ids:
            raise ValueError("Ask requires at least one paper id or a table with papers")

        papers = self._papers_by_id(session, resolved_paper_ids)
        missing = [paper_id for paper_id in resolved_paper_ids if paper_id not in papers]
        if missing:
            raise ValueError(f"Paper id(s) not found: {', '.join(str(item) for item in missing)}")

        ordered_papers = [papers[paper_id] for paper_id in resolved_paper_ids]
        hits = await self.retrieve(session, cleaned_question, ordered_papers, top_k=top_k)
        context_text = _format_context(hits)
        result = await self._call_baml(cleaned_question, context_text)
        payload = _to_plain(result)
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        return OneOffAnswer(
            question=cleaned_question,
            paper_ids=resolved_paper_ids,
            answer=str(payload.get("answer") or ""),
            evidence=evidence,
            confidence=_enum_to_text(payload.get("confidence")),
            rationale=payload.get("rationale"),
            raw={**payload, "retrieved_chunk_ids": [paper_hit.hit.chunk_id for paper_hit in hits]},
            retrieved_chunk_ids=[paper_hit.hit.chunk_id for paper_hit in hits],
        )

    async def retrieve(
        self,
        session: Session,
        question: str,
        papers: list[Paper],
        *,
        top_k: int | None = None,
    ) -> list[PaperHit]:
        self.settings.require_gateway()
        per_paper_k = top_k or min(self.settings.top_k, 4)
        vector = await embed_query(self.settings, question)
        hits_by_paper: list[list[PaperHit]] = []
        for paper in papers:
            if paper.id is None:
                continue
            hits = self.index.search(vector, paper_id=paper.id, limit=per_paper_k)
            if not hits:
                hits = _fallback_hits(session, paper.id, per_paper_k)
            hits_by_paper.append([PaperHit(paper=paper, hit=hit) for hit in hits])
        return _round_robin(hits_by_paper)

    async def _call_baml(self, question: str, evidence_context: str) -> Any:
        os.environ.setdefault("BAML_LOG", "OFF")
        from catena.baml_client.async_client import b

        registry = self._client_registry()
        return await b.AnswerQuestion(
            question,
            evidence_context,
            {"client_registry": registry},
        )

    def _client_registry(self) -> ClientRegistry:
        self.settings.require_gateway()
        registry = ClientRegistry()
        registry.add_llm_client(
            name="Gateway",
            provider="openai-responses",
            options={
                "base_url": self.settings.gateway_base_url,
                "api_key": self.settings.gateway_api_key,
                "model": self.settings.llm_model,
                "temperature": self.settings.llm_temperature,
            },
        )
        registry.set_primary("Gateway")
        return registry

    @staticmethod
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
            return []
        memberships = session.exec(
            select(TablePaper)
            .where(TablePaper.table_id == table_id)
            .order_by(TablePaper.created_at)
        ).all()
        return [membership.paper_id for membership in memberships]

    @staticmethod
    def _papers_by_id(session: Session, paper_ids: list[int]) -> dict[int, Paper]:
        papers = session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()  # type: ignore[union-attr]
        return {paper.id or 0: paper for paper in papers}


def _fallback_hits(session: Session, paper_id: int, limit: int) -> list[SearchHit]:
    chunks = session.exec(
        select(PaperChunk)
        .where(PaperChunk.paper_id == paper_id)
        .order_by(PaperChunk.chunk_index)
        .limit(max(1, limit))
    ).all()
    return [
        SearchHit(
            chunk_id=chunk.id or 0,
            paper_id=chunk.paper_id,
            text=chunk.text,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            heading=chunk.heading,
            score=None,
            raw={},
        )
        for chunk in chunks
    ]


def _format_context(paper_hits: list[PaperHit]) -> str:
    parts: list[str] = []
    total_chars = 0
    for paper_hit in paper_hits:
        paper = paper_hit.paper
        hit = paper_hit.hit
        page = hit.page_start or "unknown"
        header = (
            f"[paper_id={paper.id}; paper_title={paper.title}; "
            f"chunk_id={hit.chunk_id}; page={page}"
        )
        if hit.heading:
            header += f"; heading={hit.heading}"
        if hit.score is not None:
            header += f"; distance={hit.score:.4f}"
        header += "]"
        block = f"{header}\n{hit.text.strip()}"
        if total_chars + len(block) > MAX_QA_CONTEXT_CHARS:
            remaining = MAX_QA_CONTEXT_CHARS - total_chars
            if remaining <= 500:
                break
            block = block[:remaining]
        parts.append(block)
        total_chars += len(block)
    return "\n\n".join(parts)


def _round_robin(groups: list[list[PaperHit]]) -> list[PaperHit]:
    ordered: list[PaperHit] = []
    max_length = max((len(group) for group in groups), default=0)
    for index in range(max_length):
        for group in groups:
            if index < len(group):
                ordered.append(group[index])
    return ordered


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _to_plain(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return value
    data: dict[str, Any] = {}
    for key in ("answer", "evidence", "confidence", "rationale"):
        if hasattr(value, key):
            data[key] = getattr(value, key)
    return data


def _enum_to_text(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value).lower()
    name = getattr(value, "name", None)
    if name is not None:
        return str(name).lower()
    return str(value).lower()
