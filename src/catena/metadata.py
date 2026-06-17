from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class PaperMetadata:
    title: str | None = None
    doi: str | None = None
    year: int | None = None
    venue: str | None = None
    publication_date: str | None = None
    citation_count: int | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    url: str | None = None
    pdf_url: str | None = None
    sources: dict[str, Any] = field(default_factory=dict)


async def fetch_paper_metadata(
    *,
    title: str | None = None,
    doi: str | None = None,
    timeout: float = 20.0,
) -> PaperMetadata | None:
    """Fetch free bibliographic metadata inspired by paper-qa metadata clients.

    Sources are intentionally free/no-key by default:
    - OpenAlex for year, venue, citations, DOI, abstract, open-access locations.
    - Semantic Scholar as a second source for citation counts and abstracts.
    """

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        openalex = await _fetch_openalex(client, title=title, doi=doi)
        semantic_scholar = await _fetch_semantic_scholar(client, title=title, doi=doi)

    if openalex is None and semantic_scholar is None:
        return None
    return _merge_metadata(openalex, semantic_scholar)


async def _fetch_openalex(
    client: httpx.AsyncClient,
    *,
    title: str | None,
    doi: str | None,
) -> PaperMetadata | None:
    try:
        if doi:
            url = f"https://api.openalex.org/works/doi:{quote(normalize_doi(doi), safe='')}"
            response = await client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return _openalex_to_metadata(response.json())
        if title:
            response = await client.get(
                "https://api.openalex.org/works",
                params={"search": title, "per-page": 1},
            )
            response.raise_for_status()
            results = response.json().get("results") or []
            if results:
                return _openalex_to_metadata(results[0])
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return None


async def _fetch_semantic_scholar(
    client: httpx.AsyncClient,
    *,
    title: str | None,
    doi: str | None,
) -> PaperMetadata | None:
    fields = "title,year,venue,citationCount,abstract,authors,url,openAccessPdf,externalIds"
    try:
        if doi:
            paper_id = f"DOI:{normalize_doi(doi)}"
            response = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/{quote(paper_id, safe=':')}",
                params={"fields": fields},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return _semantic_scholar_to_metadata(response.json())
        if title:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": title, "limit": 1, "fields": fields},
            )
            response.raise_for_status()
            results = response.json().get("data") or []
            if results:
                return _semantic_scholar_to_metadata(results[0])
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return None


def _openalex_to_metadata(item: dict[str, Any]) -> PaperMetadata:
    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    best_oa_location = item.get("best_oa_location") or {}
    authors = [
        author.get("author", {}).get("display_name")
        for author in item.get("authorships") or []
        if author.get("author", {}).get("display_name")
    ]
    return PaperMetadata(
        title=item.get("title") or item.get("display_name"),
        doi=normalize_doi(item.get("doi")),
        year=item.get("publication_year"),
        venue=source.get("display_name") or item.get("host_venue", {}).get("display_name"),
        publication_date=item.get("publication_date"),
        citation_count=item.get("cited_by_count"),
        abstract=_abstract_from_openalex(item.get("abstract_inverted_index")),
        authors=authors,
        url=item.get("id") or item.get("doi"),
        pdf_url=best_oa_location.get("pdf_url") or primary_location.get("pdf_url"),
        sources={"openalex": item},
    )


def _semantic_scholar_to_metadata(item: dict[str, Any]) -> PaperMetadata:
    external_ids = item.get("externalIds") or {}
    open_access_pdf = item.get("openAccessPdf") or {}
    return PaperMetadata(
        title=item.get("title"),
        doi=normalize_doi(external_ids.get("DOI")),
        year=item.get("year"),
        venue=item.get("venue"),
        citation_count=item.get("citationCount"),
        abstract=item.get("abstract"),
        authors=[author.get("name") for author in item.get("authors") or [] if author.get("name")],
        url=item.get("url"),
        pdf_url=open_access_pdf.get("url"),
        sources={"semantic_scholar": item},
    )


def _merge_metadata(*items: PaperMetadata | None) -> PaperMetadata:
    present = [item for item in items if item is not None]
    sources: dict[str, Any] = {}
    for item in present:
        sources.update(item.sources)
    return PaperMetadata(
        title=_first(item.title for item in present),
        doi=normalize_doi(_first(item.doi for item in present)),
        year=_first(item.year for item in present),
        venue=_first(item.venue for item in present),
        publication_date=_first(item.publication_date for item in present),
        citation_count=_max_int(item.citation_count for item in present),
        abstract=_first(item.abstract for item in present),
        authors=_first_list(item.authors for item in present),
        url=_first(item.url for item in present),
        pdf_url=_first(item.pdf_url for item in present),
        sources=sources,
    )


def _abstract_from_openalex(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, word_positions in inverted_index.items():
        positions.extend((position, word) for position in word_positions)
    return " ".join(word for _, word in sorted(positions)) or None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    normalized = normalized.removeprefix("https://doi.org/")
    normalized = normalized.removeprefix("http://doi.org/")
    normalized = normalized.removeprefix("doi:")
    return normalized.strip() or None


def _first(values: object) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _first_list(values: object) -> list[str]:
    for value in values:
        if value:
            return list(value)
    return []


def _max_int(values: object) -> int | None:
    ints = [value for value in values if isinstance(value, int)]
    return max(ints) if ints else None
