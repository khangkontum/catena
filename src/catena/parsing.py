from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARSER_HASH = "docling-hybrid-v1"


@dataclass(frozen=True)
class ParsedChunk:
    index: int
    text: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    docling_json: dict[str, Any]
    chunks: list[ParsedChunk]


def parse_pdf(path: Path) -> ParsedDocument:
    """Parse a PDF with Docling and return markdown plus retrieval chunks."""

    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(path)
    document = result.document
    markdown = document.export_to_markdown()
    docling_json = document.export_to_dict()

    chunker = HybridChunker()
    chunks: list[ParsedChunk] = []
    for index, chunk in enumerate(chunker.chunk(dl_doc=document)):
        text = _chunk_text(chunker, chunk)
        if not text.strip():
            continue
        metadata = _chunk_metadata(chunk)
        page_start, page_end = _extract_page_range(metadata, chunk)
        heading = _extract_heading(metadata, chunk)
        chunks.append(
            ParsedChunk(
                index=len(chunks),
                text=text,
                page_start=page_start,
                page_end=page_end,
                heading=heading,
                metadata={"docling_chunk_index": index, **metadata},
            )
        )

    return ParsedDocument(markdown=markdown, docling_json=docling_json, chunks=chunks)


def _chunk_text(chunker: Any, chunk: Any) -> str:
    try:
        contextualized = chunker.contextualize(chunk)
        if contextualized:
            return str(contextualized)
    except Exception:
        pass
    return str(getattr(chunk, "text", ""))


def _chunk_metadata(chunk: Any) -> dict[str, Any]:
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return {}
    if hasattr(meta, "export_json_dict"):
        try:
            exported = meta.export_json_dict()
            if isinstance(exported, dict):
                return exported
        except Exception:
            pass
    if hasattr(meta, "model_dump"):
        try:
            exported = meta.model_dump(mode="json")
            if isinstance(exported, dict):
                return exported
        except Exception:
            pass
    return {"repr": repr(meta)}


def _extract_page_range(metadata: dict[str, Any], chunk: Any) -> tuple[int | None, int | None]:
    pages: list[int] = []
    origin = metadata.get("origin")
    if isinstance(origin, dict):
        page = origin.get("page_no") or origin.get("page")
        if isinstance(page, int):
            pages.append(page)

    for item in metadata.get("doc_items") or []:
        if not isinstance(item, dict):
            continue
        for prov in item.get("prov") or []:
            if isinstance(prov, dict) and isinstance(prov.get("page_no"), int):
                pages.append(prov["page_no"])

    meta = getattr(chunk, "meta", None)
    origin_obj = getattr(meta, "origin", None)
    page = getattr(origin_obj, "page_no", None)
    if isinstance(page, int):
        pages.append(page)

    if not pages:
        return None, None
    return min(pages), max(pages)


def _extract_heading(metadata: dict[str, Any], chunk: Any) -> str | None:
    headings = metadata.get("headings")
    if isinstance(headings, list) and headings:
        return " > ".join(str(item) for item in headings if item)
    meta = getattr(chunk, "meta", None)
    headings_obj = getattr(meta, "headings", None)
    if headings_obj:
        return " > ".join(str(item) for item in headings_obj if item)
    return None
