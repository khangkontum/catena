from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from catena.models import Paper

SortKey = Literal["created", "title", "year", "citations"]


@dataclass(frozen=True)
class PaperFilter:
    tags_all: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    tags_none: list[str] = field(default_factory=list)
    untagged: bool = False
    year_min: int | None = None
    year_max: int | None = None
    citations_min: int | None = None
    citations_max: int | None = None
    title_contains: str | None = None
    venue_contains: str | None = None
    has_doi: bool = False
    missing_doi: bool = False
    has_pdf: bool = False
    parsed_only: bool = False
    indexed_only: bool = False
    limit: int | None = None
    sort_by: SortKey = "created"
    descending: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "tags_all": self.tags_all,
            "tags_any": self.tags_any,
            "tags_none": self.tags_none,
            "untagged": self.untagged,
            "year_min": self.year_min,
            "year_max": self.year_max,
            "citations_min": self.citations_min,
            "citations_max": self.citations_max,
            "title_contains": self.title_contains,
            "venue_contains": self.venue_contains,
            "has_doi": self.has_doi,
            "missing_doi": self.missing_doi,
            "has_pdf": self.has_pdf,
            "parsed_only": self.parsed_only,
            "indexed_only": self.indexed_only,
            "limit": self.limit,
            "sort_by": self.sort_by,
            "descending": self.descending,
        }


def matches_filter(paper: Paper, tag_names: set[str], paper_filter: PaperFilter) -> bool:
    normalized_tags = {normalize_tag(tag) for tag in tag_names}
    tags_all = {normalize_tag(tag) for tag in paper_filter.tags_all}
    tags_any = {normalize_tag(tag) for tag in paper_filter.tags_any}
    tags_none = {normalize_tag(tag) for tag in paper_filter.tags_none}

    if tags_all and not tags_all.issubset(normalized_tags):
        return False
    if tags_any and not normalized_tags.intersection(tags_any):
        return False
    if tags_none and normalized_tags.intersection(tags_none):
        return False
    if paper_filter.untagged and normalized_tags:
        return False

    if paper_filter.year_min is not None and (
        paper.year is None or paper.year < paper_filter.year_min
    ):
        return False
    if paper_filter.year_max is not None and (
        paper.year is None or paper.year > paper_filter.year_max
    ):
        return False
    if paper_filter.citations_min is not None and (
        paper.citation_count is None or paper.citation_count < paper_filter.citations_min
    ):
        return False
    if paper_filter.citations_max is not None and (
        paper.citation_count is None or paper.citation_count > paper_filter.citations_max
    ):
        return False

    title_query = paper_filter.title_contains
    if title_query and title_query.lower() not in paper.title.lower():
        return False
    if paper_filter.venue_contains:
        venue = paper.venue or ""
        if paper_filter.venue_contains.lower() not in venue.lower():
            return False

    if paper_filter.has_doi and not paper.doi:
        return False
    if paper_filter.missing_doi and paper.doi:
        return False
    if paper_filter.has_pdf and not paper.stored_pdf_path:
        return False
    if paper_filter.parsed_only and paper.parse_status != "parsed":
        return False
    return not (paper_filter.indexed_only and paper.index_status != "indexed")


def sort_papers(papers: list[Paper], paper_filter: PaperFilter) -> list[Paper]:
    sorted_papers = sorted(
        papers,
        key=lambda paper: _sort_value(paper, paper_filter.sort_by),
        reverse=paper_filter.descending,
    )
    if paper_filter.limit is not None:
        return sorted_papers[: max(0, paper_filter.limit)]
    return sorted_papers


def normalize_tag(tag: str) -> str:
    return "-".join(tag.strip().lower().split())


def _sort_value(paper: Paper, sort_by: SortKey) -> Any:
    if sort_by == "title":
        return (paper.title or "").lower()
    if sort_by == "year":
        return paper.year if paper.year is not None else -1
    if sort_by == "citations":
        return paper.citation_count if paper.citation_count is not None else -1
    return paper.created_at
