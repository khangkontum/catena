from __future__ import annotations

import gc
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

PARSER_HASH = "docling-hybrid-v1"
MIN_TEXT_LAYER_CHARS = 500
_SUCCESS_STATUSES = {"success", "partial_success"}
_PARSER_LOCK = RLock()
_PARSER: DoclingParser | None = None
_log = logging.getLogger(__name__)


class _RapidOcrEmptyResultFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "RapidOCR returned empty result!"


logging.getLogger("docling.models.stages.ocr.rapid_ocr_model").addFilter(
    _RapidOcrEmptyResultFilter()
)


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


@dataclass(frozen=True)
class ParsedPdfResult:
    path: Path
    document: ParsedDocument | None
    error: str | None = None


class DoclingParser:
    """Docling parser configured per document to avoid unnecessary OCR."""

    def __init__(self, *, do_ocr: bool) -> None:
        from docling.chunking import HybridChunker
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption

        self._lock = RLock()
        pipeline_options = _pdf_pipeline_options(do_ocr=do_ocr)
        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
        self._converter.initialize_pipeline(InputFormat.PDF)
        self._chunker = HybridChunker()

    def parse_pdf(self, path: Path) -> ParsedDocument:
        with self._lock:
            result = self._converter.convert(path)
            parsed = _parse_conversion_result(path, result, self._chunker)
        if parsed.document is None:
            raise RuntimeError(parsed.error or f"Docling conversion failed for {path}")
        return parsed.document

    def parse_pdfs(self, paths: list[Path]) -> list[ParsedPdfResult]:
        if not paths:
            return []
        return [_parse_pdf_result(path) for path in paths]


def preload_docling_models() -> None:
    """Initialize Docling's PDF pipeline once for subsequent single parsing."""

    _get_parser()


def parse_pdf(path: Path) -> ParsedDocument:
    """Parse a PDF, skipping OCR when its text layer is sufficient."""

    parsed = _parse_pdf_result(path)
    if parsed.document is None:
        raise RuntimeError(parsed.error or f"Docling conversion failed for {path}")
    return parsed.document


def parse_pdfs(paths: list[Path]) -> list[ParsedPdfResult]:
    """Parse PDFs one by one so status, errors, and memory are isolated per paper."""

    return [_parse_pdf_result(path) for path in paths]


def _get_parser() -> DoclingParser:
    global _PARSER  # noqa: PLW0603 - singleton cache for model reuse
    with _PARSER_LOCK:
        if _PARSER is None:
            _PARSER = DoclingParser(do_ocr=True)
        return _PARSER


def _parse_pdf_result(path: Path) -> ParsedPdfResult:
    parser: DoclingParser | None = None
    try:
        parser = DoclingParser(do_ocr=not has_sufficient_text_layer(path))
        return ParsedPdfResult(path=path, document=parser.parse_pdf(path))
    except Exception as exc:
        return ParsedPdfResult(path=path, document=None, error=str(exc))
    finally:
        del parser
        gc.collect()


def has_sufficient_text_layer(path: Path, *, min_chars: int = MIN_TEXT_LAYER_CHARS) -> bool:
    """Return whether a PDF has enough extractable text to skip OCR."""

    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(path)
        try:
            chars = 0
            page_count = len(document)
            pages_to_check = min(page_count, 5)
            if pages_to_check == 0:
                return False
            for page_index in range(pages_to_check):
                page = document[page_index]
                text_page = page.get_textpage()
                chars += len(text_page.get_text_range().strip())
                text_page.close()
                page.close()
                if chars >= min_chars:
                    return True
            return chars >= max(100, min_chars // 5)
        finally:
            document.close()
    except Exception as exc:
        _log.debug("Could not inspect PDF text layer for %s: %s", path, exc)
        return False


def _pdf_pipeline_options(*, do_ocr: bool) -> Any:
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import (
        OcrAutoOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
    )

    device = _preferred_docling_device()
    options = PdfPipelineOptions(
        do_ocr=do_ocr,
        accelerator_options=AcceleratorOptions(device=device),
    )
    if not do_ocr:
        return options
    if device == "mps":
        options.ocr_options = RapidOcrOptions(
            lang=["english"],
            backend="torch",
            print_verbose=False,
            rapidocr_params={"EngineConfig.torch.use_mps": True},
        )
    else:
        options.ocr_options = OcrAutoOptions(force_full_page_ocr=False)
    return options


def _preferred_docling_device() -> str:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "auto"


def _parse_conversion_result(path: Path, result: Any, chunker: Any) -> ParsedPdfResult:
    status = str(getattr(result, "status", "")).lower().split(".")[-1]
    if status not in _SUCCESS_STATUSES or getattr(result, "document", None) is None:
        return ParsedPdfResult(path=path, document=None, error=_conversion_error(path, result))
    document = result.document
    markdown = document.export_to_markdown()
    docling_json = document.export_to_dict()
    return ParsedPdfResult(
        path=path,
        document=ParsedDocument(
            markdown=markdown,
            docling_json=docling_json,
            chunks=_chunks_from_document(document, chunker),
        ),
    )


def _chunks_from_document(document: Any, chunker: Any) -> list[ParsedChunk]:
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
    return chunks


def _conversion_error(path: Path, result: Any) -> str:
    status = getattr(result, "status", "unknown")
    errors = getattr(result, "errors", None) or []
    messages = [str(getattr(error, "error_message", error)) for error in errors]
    details = f": {'; '.join(messages)}" if messages else ""
    return f"Docling conversion failed for {path} with status {status}{details}"


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
