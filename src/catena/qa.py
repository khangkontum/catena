from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from baml_py import ClientRegistry
from sqlmodel import Session, select

from catena.config import Settings
from catena.embeddings import embed_query
from catena.models import ExtractionCell, ExtractionColumn, Paper, PaperChunk, Status, TablePaper
from catena.vector import LanceIndex, SearchHit

MAX_QA_CONTEXT_CHARS = 32_000
MAX_MATRIX_CONTEXT_CHARS = 32_000
MAX_SYNTHESIS_CONTEXT_CHARS = 32_000
SYNTHESIS_TABLE_THRESHOLD = 8
AskMode = Literal["auto", "fast", "synthesis", "matrix"]
_VALID_ASK_MODES: set[str] = {"auto", "fast", "synthesis", "matrix"}
_SYNTHESIS_TERMS = {
    "across",
    "aggregate",
    "aggregated",
    "all",
    "common",
    "compare",
    "compared",
    "comparison",
    "count",
    "distribution",
    "each",
    "every",
    "how many",
    "least",
    "list",
    "matrix",
    "most",
    "overall",
    "summarize",
    "table",
    "trend",
    "whole",
}
_COMPLETED_CELL_STATUSES = {Status.ANSWERED, Status.NOT_REPORTED, Status.UNCERTAIN}


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
    mode: str = "fast"


@dataclass(frozen=True)
class PaperHit:
    paper: Paper
    hit: SearchHit


@dataclass(frozen=True)
class MatrixContext:
    text: str
    cell_count: int
    column_count: int
    relevant: bool


@dataclass(frozen=True)
class IntermediateAnswer:
    label: str
    paper_ids: list[int]
    answer: str
    evidence: list[dict[str, Any]]
    confidence: str | None
    rationale: str | None
    retrieved_chunk_ids: list[int]


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
        mode: AskMode = "auto",
        max_context_chars: int | None = None,
        batch_size: int | None = None,
    ) -> OneOffAnswer:
        cleaned_question = question.strip()
        if not cleaned_question:
            raise ValueError("Question cannot be empty")
        if mode not in _VALID_ASK_MODES:
            raise ValueError(f"Ask mode must be one of: {', '.join(sorted(_VALID_ASK_MODES))}")
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
        context_limit = max_context_chars or MAX_QA_CONTEXT_CHARS
        matrix_context = (
            _matrix_context(session, cleaned_question, table_id, max_chars=context_limit)
            if table_id is not None and not paper_ids
            else None
        )
        selected_mode = _select_mode(
            mode,
            cleaned_question,
            ordered_papers,
            matrix_context=matrix_context,
        )
        if selected_mode == "matrix":
            if matrix_context is None or not matrix_context.text:
                selected_mode = _select_mode(
                    "auto",
                    cleaned_question,
                    ordered_papers,
                    matrix_context=None,
                )
            else:
                return await self._ask_with_context(
                    cleaned_question,
                    resolved_paper_ids,
                    matrix_context.text,
                    mode="matrix",
                    retrieved_chunk_ids=[],
                    raw_extra={
                        "matrix_cell_count": matrix_context.cell_count,
                        "matrix_column_count": matrix_context.column_count,
                    },
                )

        if selected_mode == "synthesis":
            return await self._ask_with_synthesis(
                session,
                cleaned_question,
                ordered_papers,
                top_k=top_k,
                max_context_chars=context_limit,
                batch_size=batch_size,
            )

        hits = await self.retrieve(
            session,
            cleaned_question,
            ordered_papers,
            top_k=top_k,
            mode="fast",
        )
        context_text = _format_context(hits, max_chars=context_limit)
        return await self._ask_with_context(
            cleaned_question,
            resolved_paper_ids,
            context_text,
            mode="fast",
            retrieved_chunk_ids=[paper_hit.hit.chunk_id for paper_hit in hits],
        )

    async def _ask_with_context(
        self,
        question: str,
        paper_ids: list[int],
        context_text: str,
        *,
        mode: str,
        retrieved_chunk_ids: list[int],
        raw_extra: dict[str, Any] | None = None,
    ) -> OneOffAnswer:
        result = await self._call_baml(question, context_text)
        payload = _to_plain(result)
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        return OneOffAnswer(
            question=question,
            paper_ids=paper_ids,
            answer=str(payload.get("answer") or ""),
            evidence=evidence,
            confidence=_enum_to_text(payload.get("confidence")),
            rationale=payload.get("rationale"),
            raw={
                **payload,
                **(raw_extra or {}),
                "mode": mode,
                "retrieved_chunk_ids": retrieved_chunk_ids,
            },
            retrieved_chunk_ids=retrieved_chunk_ids,
            mode=mode,
        )

    async def _ask_with_synthesis(
        self,
        session: Session,
        question: str,
        papers: list[Paper],
        *,
        top_k: int | None,
        max_context_chars: int,
        batch_size: int | None,
    ) -> OneOffAnswer:
        self.settings.require_gateway()
        groups = _chunks(papers, max(1, batch_size or 1))
        vector = await embed_query(self.settings, question)
        intermediate: list[IntermediateAnswer] = []
        for index, group in enumerate(groups, start=1):
            hits = self._retrieval_hits_for_group(session, vector, group, top_k=top_k)
            label = _group_label(index, group)
            context_text = _format_context(hits, max_chars=max_context_chars)
            result = await self._call_baml_map(question, label, context_text)
            payload = _to_plain(result)
            evidence = payload.get("evidence") or []
            if not isinstance(evidence, list):
                evidence = []
            intermediate.append(
                IntermediateAnswer(
                    label=label,
                    paper_ids=[paper.id or 0 for paper in group if paper.id is not None],
                    answer=str(payload.get("answer") or ""),
                    evidence=evidence,
                    confidence=_enum_to_text(payload.get("confidence")),
                    rationale=payload.get("rationale"),
                    retrieved_chunk_ids=[paper_hit.hit.chunk_id for paper_hit in hits],
                )
            )

        synthesis_context = _format_intermediate_answers(intermediate)
        result = await self._call_baml_reduce(question, synthesis_context)
        payload = _to_plain(result)
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        retrieved_chunk_ids = [
            chunk_id for answer in intermediate for chunk_id in answer.retrieved_chunk_ids
        ]
        return OneOffAnswer(
            question=question,
            paper_ids=[paper.id or 0 for paper in papers if paper.id is not None],
            answer=str(payload.get("answer") or ""),
            evidence=evidence,
            confidence=_enum_to_text(payload.get("confidence")),
            rationale=payload.get("rationale"),
            raw={
                **payload,
                "mode": "synthesis",
                "intermediate_count": len(intermediate),
                "retrieved_chunk_ids": retrieved_chunk_ids,
            },
            retrieved_chunk_ids=retrieved_chunk_ids,
            mode="synthesis",
        )

    async def retrieve(
        self,
        session: Session,
        question: str,
        papers: list[Paper],
        *,
        top_k: int | None = None,
        mode: Literal["balanced", "fast"] = "balanced",
    ) -> list[PaperHit]:
        self.settings.require_gateway()
        vector = await embed_query(self.settings, question)
        if mode == "fast":
            return self._global_retrieval_hits(session, vector, papers, top_k=top_k)
        return self._retrieval_hits_for_group(session, vector, papers, top_k=top_k)

    def _global_retrieval_hits(
        self,
        session: Session,
        vector: list[float],
        papers: list[Paper],
        *,
        top_k: int | None = None,
    ) -> list[PaperHit]:
        paper_ids = {paper.id for paper in papers if paper.id is not None}
        papers_by_id = {paper.id or 0: paper for paper in papers if paper.id is not None}
        limit = _global_limit(top_k, self.settings.top_k, len(papers))
        hits = self.index.search_many(vector, paper_ids=paper_ids, limit=limit * 2)
        selected = _diversify_hits(hits, limit=limit)
        if not selected:
            return self._retrieval_hits_for_group(session, vector, papers, top_k=top_k)
        return [
            PaperHit(paper=papers_by_id[hit.paper_id], hit=hit)
            for hit in selected
            if hit.paper_id in papers_by_id
        ]

    def _retrieval_hits_for_group(
        self,
        session: Session,
        vector: list[float],
        papers: list[Paper],
        *,
        top_k: int | None = None,
    ) -> list[PaperHit]:
        per_paper_k = top_k or min(self.settings.top_k, 4)
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

    async def _call_baml_map(
        self,
        question: str,
        context_label: str,
        evidence_context: str,
    ) -> Any:
        os.environ.setdefault("BAML_LOG", "OFF")
        from catena.baml_client.async_client import b

        registry = self._client_registry()
        return await b.AnswerQuestionFromContext(
            question,
            context_label,
            evidence_context,
            {"client_registry": registry},
        )

    async def _call_baml_reduce(self, question: str, intermediate_answers: str) -> Any:
        os.environ.setdefault("BAML_LOG", "OFF")
        from catena.baml_client.async_client import b

        registry = self._client_registry()
        return await b.SynthesizeQuestionAnswers(
            question,
            intermediate_answers,
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


def _format_context(paper_hits: list[PaperHit], *, max_chars: int = MAX_QA_CONTEXT_CHARS) -> str:
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
        if total_chars + len(block) > max_chars:
            remaining = max_chars - total_chars
            if remaining <= 500:
                break
            block = block[:remaining]
        parts.append(block)
        total_chars += len(block)
    return "\n\n".join(parts)


def _matrix_context(
    session: Session,
    question: str,
    table_id: int | None,
    *,
    max_chars: int = MAX_MATRIX_CONTEXT_CHARS,
) -> MatrixContext | None:
    if table_id is None:
        return None
    columns = list(
        session.exec(
            select(ExtractionColumn)
            .where(ExtractionColumn.table_id == table_id)
            .order_by(ExtractionColumn.id)
        ).all()
    )
    if not columns:
        return MatrixContext("", 0, 0, False)

    memberships = list(
        session.exec(
            select(TablePaper)
            .where(TablePaper.table_id == table_id)
            .order_by(TablePaper.created_at)
        ).all()
    )
    paper_ids = [membership.paper_id for membership in memberships]
    if not paper_ids:
        return MatrixContext("", 0, len(columns), False)
    papers = session.exec(select(Paper).where(Paper.id.in_(paper_ids))).all()  # type: ignore[union-attr]
    papers_by_id = {paper.id: paper for paper in papers}
    cells = list(
        session.exec(
            select(ExtractionCell)
            .where(
                ExtractionCell.table_id == table_id,
                ExtractionCell.status.in_(_COMPLETED_CELL_STATUSES),
            )
            .order_by(ExtractionCell.paper_id, ExtractionCell.column_id)
        ).all()
    )
    if not cells:
        return MatrixContext("", 0, len(columns), False)

    columns_by_id = {column.id: column for column in columns}
    cells_by_paper: dict[int, list[ExtractionCell]] = {}
    for cell in cells:
        cells_by_paper.setdefault(cell.paper_id, []).append(cell)

    parts = [f"[table_id={table_id}; source=extraction_matrix]"]
    total_chars = len(parts[0])
    for paper_id in paper_ids:
        paper = papers_by_id.get(paper_id)
        paper_cells = cells_by_paper.get(paper_id, [])
        if paper is None or not paper_cells:
            continue
        lines = [f"[paper_id={paper.id}; paper_title={paper.title}]"]
        for cell in paper_cells:
            column = columns_by_id.get(cell.column_id)
            if column is None:
                continue
            answer = cell.answer_text or cell.status
            line = (
                f"- column_id={column.id}; column={column.name}; status={cell.status}; "
                f"answer={answer}"
            )
            if cell.confidence:
                line += f"; confidence={cell.confidence}"
            evidence = cell.evidence_json or []
            if evidence:
                quotes = []
                for item in evidence[:2]:
                    quote = str(item.get("quote") or "").strip()
                    if quote:
                        page = item.get("page")
                        chunk_id = item.get("chunk_id")
                        quotes.append(f"page={page}; chunk_id={chunk_id}; quote={quote}")
                if quotes:
                    line += "; evidence=" + " | ".join(quotes)
            lines.append(line)
        block = "\n".join(lines)
        if total_chars + len(block) > max_chars:
            break
        parts.append(block)
        total_chars += len(block)

    text = "\n\n".join(parts) if len(parts) > 1 else ""
    return MatrixContext(
        text=text,
        cell_count=len(cells),
        column_count=len(columns),
        relevant=_matrix_relevant(question, columns),
    )


def _select_mode(
    requested: AskMode,
    question: str,
    papers: list[Paper],
    *,
    matrix_context: MatrixContext | None,
) -> str:
    if requested != "auto":
        return requested
    if matrix_context is not None and matrix_context.text and matrix_context.relevant:
        return "matrix"
    if len(papers) > SYNTHESIS_TABLE_THRESHOLD or (
        len(papers) > 1 and _asks_for_synthesis(question)
    ):
        return "synthesis"
    return "fast"


def _asks_for_synthesis(question: str) -> bool:
    folded = question.casefold()
    return any(term in folded for term in _SYNTHESIS_TERMS)


def _matrix_relevant(question: str, columns: list[ExtractionColumn]) -> bool:
    query_tokens = _tokens(question)
    if not query_tokens:
        return False
    if {"table", "matrix", "columns", "column"} & query_tokens:
        return True
    for column in columns:
        column_tokens = _tokens(f"{column.name} {column.prompt}")
        if query_tokens & column_tokens:
            return True
    return len(columns) <= 6


def _tokens(value: str) -> set[str]:
    raw = [item.casefold() for item in re.findall(r"[A-Za-z0-9]+", value)]
    tokens = {item[:-1] if item.endswith("s") and len(item) > 3 else item for item in raw}
    return {item for item in tokens if len(item) > 2}


def _global_limit(top_k: int | None, default_top_k: int, paper_count: int) -> int:
    if top_k is not None:
        return max(1, top_k)
    return max(1, max(default_top_k, min(max(paper_count * 2, default_top_k), 48)))


def _diversify_hits(hits: list[SearchHit], *, limit: int) -> list[SearchHit]:
    if not hits:
        return []
    max_per_paper = max(1, min(4, (limit + 3) // 4))
    counts: dict[int, int] = {}
    selected: list[SearchHit] = []
    deferred: list[SearchHit] = []
    for hit in hits:
        count = counts.get(hit.paper_id, 0)
        if count < max_per_paper:
            selected.append(hit)
            counts[hit.paper_id] = count + 1
        else:
            deferred.append(hit)
        if len(selected) >= limit:
            return selected
    for hit in deferred:
        selected.append(hit)
        if len(selected) >= limit:
            break
    return selected


def _chunks(values: list[Paper], size: int) -> list[list[Paper]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _group_label(index: int, papers: list[Paper]) -> str:
    ids = ", ".join(str(paper.id) for paper in papers if paper.id is not None)
    if len(papers) == 1:
        return f"paper {ids}"
    return f"batch {index} papers {ids}"


def _format_intermediate_answers(
    answers: list[IntermediateAnswer],
    *,
    max_chars: int = MAX_SYNTHESIS_CONTEXT_CHARS,
) -> str:
    parts: list[str] = []
    total_chars = 0
    for answer in answers:
        lines = [
            f"[{answer.label}; paper_ids={', '.join(str(item) for item in answer.paper_ids)}]",
            f"answer: {answer.answer}",
        ]
        if answer.confidence:
            lines.append(f"confidence: {answer.confidence}")
        if answer.rationale:
            lines.append(f"rationale: {answer.rationale}")
        for item in answer.evidence[:4]:
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            lines.append(
                "- evidence: "
                f"paper_id={item.get('paper_id')}; "
                f"paper_title={item.get('paper_title')}; "
                f"page={item.get('page')}; "
                f"chunk_id={item.get('chunk_id')}; "
                f"quote={quote}"
            )
        block = "\n".join(lines)
        if total_chars + len(block) > max_chars:
            break
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
