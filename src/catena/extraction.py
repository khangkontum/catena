from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from baml_py import ClientRegistry
from sqlmodel import Session, select

from catena.config import Settings
from catena.embeddings import embed_query
from catena.models import ExtractionCell, ExtractionColumn, Paper, PaperChunk, Status, utcnow
from catena.vector import LanceIndex, SearchHit

MAX_CONTEXT_CHARS = 24_000


@dataclass(frozen=True)
class RetrievedContext:
    chunks: list[SearchHit]
    context_text: str


class ExtractionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.index = LanceIndex(settings)

    async def extract_cell(self, session: Session, cell_id: int) -> ExtractionCell:
        cell = session.get(ExtractionCell, cell_id)
        if cell is None:
            raise ValueError(f"ExtractionCell {cell_id} not found")
        paper = session.get(Paper, cell.paper_id)
        column = session.get(ExtractionColumn, cell.column_id)
        if paper is None or column is None:
            raise ValueError(f"ExtractionCell {cell_id} points to missing paper or column")
        if column.table_id != cell.table_id:
            raise ValueError(
                f"ExtractionCell {cell_id} table does not match column {column.id} table"
            )

        self.settings.require_gateway()
        cell.status = Status.RUNNING
        cell.error = None
        cell.updated_at = utcnow()
        session.add(cell)
        session.commit()
        session.refresh(cell)

        try:
            retrieved = await self.retrieve(session, paper, column)
            result = await self._call_baml(paper, column, retrieved.context_text)
            payload = _to_plain(result)
            status = _status_from_result(payload.get("status"))
            cell.status = status
            cell.answer_text = str(payload.get("answer") or "")
            value = payload.get("value")
            cell.value_json = {"value": value} if value is not None else None
            evidence = payload.get("evidence") or []
            cell.evidence_json = evidence if isinstance(evidence, list) else []
            cell.confidence = _enum_to_text(payload.get("confidence"))
            cell.raw_json = {
                **payload,
                "retrieved_chunk_ids": [hit.chunk_id for hit in retrieved.chunks],
            }
            cell.error = None
        except Exception as exc:
            cell.status = Status.FAILED
            cell.error = str(exc)
        finally:
            cell.updated_at = utcnow()
            session.add(cell)
            session.commit()
            session.refresh(cell)
        return cell

    async def retrieve(
        self,
        session: Session,
        paper: Paper,
        column: ExtractionColumn,
    ) -> RetrievedContext:
        query = column.retrieval_query or f"{column.name}\n{column.prompt}"
        top_k = column.top_k or self.settings.top_k
        vector = await embed_query(self.settings, query)
        hits = self.index.search(vector, paper_id=paper.id or 0, limit=top_k)
        if not hits:
            hits = _fallback_hits(session, paper.id or 0, top_k)
        context_text = _format_context(hits)
        return RetrievedContext(chunks=hits, context_text=context_text)

    async def _call_baml(
        self,
        paper: Paper,
        column: ExtractionColumn,
        evidence_context: str,
    ) -> Any:
        os.environ.setdefault("BAML_LOG", "OFF")
        from catena.baml_client.async_client import b

        registry = self._client_registry()
        return await b.ExtractCell(
            paper.title,
            column.name,
            column.prompt,
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


def _format_context(hits: list[SearchHit]) -> str:
    parts: list[str] = []
    total_chars = 0
    for hit in hits:
        header = f"[chunk_id={hit.chunk_id}; page={hit.page_start or 'unknown'}"
        if hit.heading:
            header += f"; heading={hit.heading}"
        if hit.score is not None:
            header += f"; distance={hit.score:.4f}"
        header += "]"
        block = f"{header}\n{hit.text.strip()}"
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining <= 500:
                break
            block = block[:remaining]
        parts.append(block)
        total_chars += len(block)
    return "\n\n".join(parts)


def _to_plain(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return value
    data: dict[str, Any] = {}
    for key in ("status", "answer", "value", "evidence", "confidence", "rationale"):
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


def _status_from_result(value: Any) -> str:
    status = _enum_to_text(value) or ""
    normalized = status.replace("-", "_").replace(" ", "_").lower()
    if normalized.endswith("not_reported") or normalized == "notreported":
        return Status.NOT_REPORTED
    if normalized.endswith("uncertain"):
        return Status.UNCERTAIN
    return Status.ANSWERED
