from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from catena.config import Settings
from catena.db import show_db_current, show_db_history, upgrade_db
from catena.filters import PaperFilter
from catena.library import CatenaLibrary
from catena.models import ExtractionCell, ExtractionTable, Paper, PaperSimilarity, TablePaper
from catena.qa import OneOffAnswer
from catena.similarity import SimilarPaper
from catena.util import truncate

console = Console()
app = typer.Typer(help="Local evidence-backed paper extraction tables.")
tables_app = typer.Typer(help="Manage extraction tables.")
papers_app = typer.Typer(help="Manage global papers.")
tags_app = typer.Typer(help="Manage paper tags.")
columns_app = typer.Typer(help="Manage extraction columns.")
similarity_app = typer.Typer(help="Compute and inspect paper-pair similarity scores.")
db_app = typer.Typer(help="Manage Alembic database migrations.")
app.add_typer(tables_app, name="tables")
app.add_typer(papers_app, name="papers")
app.add_typer(tags_app, name="tags")
app.add_typer(columns_app, name="columns")
app.add_typer(similarity_app, name="similarity")
app.add_typer(db_app, name="db")


def _library() -> CatenaLibrary:
    return CatenaLibrary(Settings.from_env())


def _resolve_table_id(library: CatenaLibrary, table_id: int | None) -> int:
    return table_id if table_id is not None else library.default_table_id()


def _build_paper_filter(
    *,
    tag_all: list[str] | None = None,
    tag_any: list[str] | None = None,
    tag_none: list[str] | None = None,
    untagged: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
    citations_min: int | None = None,
    citations_max: int | None = None,
    title_contains: str | None = None,
    venue_contains: str | None = None,
    has_doi: bool = False,
    missing_doi: bool = False,
    has_pdf: bool = False,
    parsed_only: bool = False,
    indexed_only: bool = False,
    limit: int | None = None,
    sort_by: str = "created",
    descending: bool = False,
) -> PaperFilter:
    if sort_by not in {"created", "title", "year", "citations"}:
        raise typer.BadParameter("--sort-by must be one of: created, title, year, citations")
    return PaperFilter(
        tags_all=tag_all or [],
        tags_any=tag_any or [],
        tags_none=tag_none or [],
        untagged=untagged,
        year_min=year_min,
        year_max=year_max,
        citations_min=citations_min,
        citations_max=citations_max,
        title_contains=title_contains,
        venue_contains=venue_contains,
        has_doi=has_doi,
        missing_doi=missing_doi,
        has_pdf=has_pdf,
        parsed_only=parsed_only,
        indexed_only=indexed_only,
        limit=limit,
        sort_by=sort_by,  # type: ignore[arg-type]
        descending=descending,
    )


@app.command()
def init() -> None:
    """Initialize local storage."""

    library = _library()
    library.init()
    console.print(f"[green]Initialized[/green] {library.settings.data_dir}")


@app.command()
def config() -> None:
    """Show resolved local paths and gateway readiness."""

    settings = Settings.from_env()
    table = Table(title="catena config")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("data_dir", str(settings.data_dir))
    table.add_row("sqlite", str(settings.sqlite_path))
    table.add_row("lancedb", str(settings.lancedb_uri))
    table.add_row("gateway_ready", "yes" if settings.gateway_ready else "no")
    table.add_row("llm_model", settings.llm_model or "")
    table.add_row("embedding_model", settings.embedding_model or "")
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Reload on source changes."),
) -> None:
    """Run the local FastAPI server for the web frontend."""

    import uvicorn

    uvicorn.run("catena.api:create_app", host=host, port=port, reload=reload, factory=True)


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head", help="Alembic revision.")) -> None:
    """Run Alembic migrations."""

    library = _library()
    upgrade_db(library.engine, revision)
    console.print(f"[green]Database upgraded[/green] to {revision}")


@db_app.command("current")
def db_current() -> None:
    """Show current Alembic revision."""

    show_db_current(_library().engine)


@db_app.command("history")
def db_history() -> None:
    """Show Alembic migration history."""

    show_db_history()


@tables_app.command("create")
def create_table(
    name: str = typer.Argument(..., help="Table name."),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Create an extraction table."""

    table = _library().create_table(name, description)
    _print_table(table)


@tables_app.command("create-from-filter")
def create_table_from_filter(
    name: str = typer.Argument(..., help="New table name."),
    description: str | None = typer.Option(None, "--description", "-d"),
    tag_all: list[str] | None = typer.Option(None, "--tag-all", help="Require every tag."),
    tag_any: list[str] | None = typer.Option(None, "--tag-any", help="Require at least one tag."),
    tag_none: list[str] | None = typer.Option(None, "--tag-none", help="Exclude these tags."),
    untagged: bool = typer.Option(False, "--untagged", help="Only papers with no tags."),
    year_min: int | None = typer.Option(None, "--year-min", help="Minimum publication year."),
    year_max: int | None = typer.Option(None, "--year-max", help="Maximum publication year."),
    citations_min: int | None = typer.Option(None, "--citations-min"),
    citations_max: int | None = typer.Option(None, "--citations-max"),
    title_contains: str | None = typer.Option(None, "--title-contains"),
    venue_contains: str | None = typer.Option(None, "--venue-contains"),
    has_doi: bool = typer.Option(False, "--has-doi"),
    missing_doi: bool = typer.Option(False, "--missing-doi"),
    has_pdf: bool = typer.Option(False, "--has-pdf"),
    parsed_only: bool = typer.Option(False, "--parsed-only"),
    indexed_only: bool = typer.Option(False, "--indexed-only"),
    limit: int | None = typer.Option(None, "--limit"),
    sort_by: str = typer.Option("created", "--sort-by", help="created|title|year|citations"),
    descending: bool = typer.Option(False, "--desc", help="Sort descending."),
) -> None:
    """Create a table from tags and metadata filters without reprocessing papers."""

    paper_filter = _build_paper_filter(
        tag_all=tag_all,
        tag_any=tag_any,
        tag_none=tag_none,
        untagged=untagged,
        year_min=year_min,
        year_max=year_max,
        citations_min=citations_min,
        citations_max=citations_max,
        title_contains=title_contains,
        venue_contains=venue_contains,
        has_doi=has_doi,
        missing_doi=missing_doi,
        has_pdf=has_pdf,
        parsed_only=parsed_only,
        indexed_only=indexed_only,
        limit=limit,
        sort_by=sort_by,
        descending=descending,
    )
    table, papers = _library().create_table_from_filter(
        name,
        paper_filter,
        description=description,
    )
    _print_table(table)
    console.print(f"  attached papers: {len(papers)}")


@tables_app.command("refresh")
def refresh_table(
    table_id: int = typer.Argument(..., help="Table id created from a saved filter."),
    prune: bool = typer.Option(False, "--prune", help="Remove papers that no longer match."),
) -> None:
    """Refresh a filtered table after adding tags or metadata."""

    papers = _library().refresh_table_from_filter(table_id, prune=prune)
    console.print(f"[green]Refreshed[/green] table {table_id}; matching papers: {len(papers)}")


@tables_app.command("list")
def list_tables() -> None:
    """List extraction tables."""

    rich_table = Table(title="Extraction tables")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Description")
    rich_table.add_column("Filtered")
    for table in _library().tables():
        rich_table.add_row(
            str(table.id),
            table.name,
            table.description or "",
            "yes" if table.source_filter_json else "no",
        )
    console.print(rich_table)


@tables_app.command("add-paper")
def add_paper_to_table(
    table_id: int = typer.Argument(..., help="Extraction table id."),
    paper_id: int = typer.Argument(..., help="Global paper id."),
) -> None:
    """Attach an existing global paper to a table and queue that table's columns."""

    membership = _library().add_paper_to_table(table_id, paper_id)
    _print_membership(membership)


@tables_app.command("papers")
def list_table_papers(
    table_id: int | None = typer.Option(None, "--table-id", help="Table id. Defaults to Default."),
) -> None:
    """List papers in one extraction table."""

    library = _library()
    resolved_table_id = _resolve_table_id(library, table_id)
    _print_papers(
        library.papers(table_id=resolved_table_id),
        title=f"Papers in table {resolved_table_id}",
    )


@papers_app.command("add")
def add_paper(
    pdf: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="PDF file to parse and index.",
    ),
    title: str | None = typer.Option(None, "--title", "-t", help="Override title."),
    doi: str | None = typer.Option(None, "--doi", help="Optional DOI."),
    url: str | None = typer.Option(None, "--url", help="Optional source URL."),
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Attach to this table after indexing. Defaults to the Default table.",
    ),
    no_table: bool = typer.Option(
        False,
        "--no-table",
        help="Only add to the global paper library; do not attach to a table.",
    ),
) -> None:
    """Parse/index a PDF once globally, optionally attaching it to a table."""

    library = _library()
    resolved_table_id = None if no_table else _resolve_table_id(library, table_id)
    paper = asyncio.run(
        library.add_pdf(pdf, title=title, doi=doi, url=url, table_id=resolved_table_id)
    )
    _print_paper(paper)
    if resolved_table_id is not None:
        console.print(f"  table: {resolved_table_id}")


@papers_app.command("list")
def list_papers(
    table_id: int | None = typer.Option(None, "--table-id", help="Only list papers in a table."),
) -> None:
    """List global papers, or papers in a specific table."""

    title = "Papers" if table_id is None else f"Papers in table {table_id}"
    _print_papers(_library().papers(table_id=table_id), title=title)


@papers_app.command("similar")
def similar_papers(
    paper_id: int = typer.Argument(..., help="Paper id to find related papers for."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum similar papers to show."),
    min_score: float | None = typer.Option(None, "--min-score", help="Minimum score to show."),
) -> None:
    """List papers with precomputed similarity scores for one paper."""

    _print_similar_papers(
        paper_id,
        _library().similar_papers(paper_id, limit=limit, min_score=min_score),
    )


@papers_app.command("set-metadata")
def set_paper_metadata(
    paper_id: int = typer.Argument(...),
    year: int | None = typer.Option(None, "--year"),
    venue: str | None = typer.Option(None, "--venue"),
    citations: int | None = typer.Option(None, "--citations"),
    doi: str | None = typer.Option(None, "--doi"),
    abstract: str | None = typer.Option(None, "--abstract"),
) -> None:
    """Manually set metadata used by table filters."""

    paper = _library().set_paper_metadata(
        paper_id,
        year=year,
        venue=venue,
        citation_count=citations,
        doi=doi,
        abstract=abstract,
    )
    _print_paper(paper)


@papers_app.command("enrich")
def enrich_paper(
    paper_id: int | None = typer.Option(None, "--paper-id", help="Paper id to enrich."),
    all_papers: bool = typer.Option(False, "--all", help="Enrich all papers."),
) -> None:
    """Fetch free metadata from OpenAlex/Semantic Scholar."""

    library = _library()
    if not all_papers and paper_id is None:
        raise typer.BadParameter("Provide --paper-id or --all.")
    if all_papers:
        paper_ids = [paper.id for paper in library.papers() if paper.id is not None]
    else:
        paper_ids = [paper_id]
    for resolved_paper_id in paper_ids:
        if resolved_paper_id is None:
            continue
        paper = asyncio.run(library.enrich_paper(resolved_paper_id))
        _print_paper(paper)


@tags_app.command("create")
def create_tag(
    name: str = typer.Argument(...),
    color: str | None = typer.Option(None, "--color"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Create or update a tag."""

    tag = _library().create_tag(name, color=color, description=description)
    console.print(f"[green]Tag[/green] {tag.id}: {tag.name}")


@tags_app.command("list")
def list_tags() -> None:
    """List tags."""

    rich_table = Table(title="Tags")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Color")
    rich_table.add_column("Description")
    for tag in _library().tags():
        rich_table.add_row(str(tag.id), tag.name, tag.color or "", tag.description or "")
    console.print(rich_table)


@tags_app.command("add")
def add_tag_to_paper(
    paper_id: int = typer.Argument(...),
    tags: list[str] = typer.Argument(..., help="One or more tag names."),
) -> None:
    """Add tag(s) to a paper."""

    library = _library()
    for tag_name in tags:
        tag = library.tag_paper(paper_id, tag_name)
        console.print(f"[green]Tagged[/green] paper {paper_id} with {tag.name}")


@tags_app.command("remove")
def remove_tag_from_paper(
    paper_id: int = typer.Argument(...),
    tag: str = typer.Argument(...),
) -> None:
    """Remove a tag from a paper."""

    _library().untag_paper(paper_id, tag)
    console.print(f"[green]Removed[/green] tag {tag} from paper {paper_id}")


@tags_app.command("papers")
def papers_by_tag(
    tag: list[str] = typer.Option(..., "--tag", help="Required tag. Repeat for AND."),
) -> None:
    """List papers that have all provided tags."""

    paper_filter = PaperFilter(tags_all=tag)
    _print_papers(_library().filter_papers(paper_filter), title="Tagged papers")


@columns_app.command("add")
def add_column(
    name: str = typer.Argument(..., help="Column name, e.g. 'Sample size'."),
    prompt: str = typer.Argument(..., help="Atomic extraction instruction."),
    table_id: int | None = typer.Option(None, "--table-id", help="Table id. Defaults to Default."),
    retrieval_query: str | None = typer.Option(
        None,
        "--retrieval-query",
        help="Optional search query override for retrieval.",
    ),
    top_k: int | None = typer.Option(None, "--top-k", help="Number of chunks to retrieve."),
    run: bool = typer.Option(False, "--run", help="Run queued cells after creating the column."),
) -> None:
    """Create an atomic extraction column in one table and queue cells for its papers."""

    library = _library()
    resolved_table_id = _resolve_table_id(library, table_id)
    column = library.add_column(
        name,
        prompt,
        table_id=resolved_table_id,
        retrieval_query=retrieval_query,
        top_k=top_k,
    )
    console.print(
        f"[green]Created column[/green] {column.id}: {column.name} "
        f"in table {resolved_table_id}"
    )
    if run:
        results = asyncio.run(library.run_pending(table_id=resolved_table_id, column_id=column.id))
        _print_run_results(results)


@columns_app.command("list")
def list_columns(
    table_id: int | None = typer.Option(None, "--table-id", help="Only list columns in a table."),
) -> None:
    """List extraction columns."""

    rich_table = Table(title="Extraction columns")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Table", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Prompt")
    rich_table.add_column("Top K")
    for column in _library().columns(table_id=table_id):
        rich_table.add_row(
            str(column.id),
            str(column.table_id),
            column.name,
            truncate(column.prompt, 90),
            str(column.top_k or "default"),
        )
    console.print(rich_table)


@similarity_app.command("compute")
def compute_similarity(
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Only compute pairs among papers in this table. Defaults to all papers.",
    ),
    paper_id: list[int] | None = typer.Option(
        None,
        "--paper-id",
        help="Paper id to include. Repeat for an explicit set of papers.",
    ),
    display_limit: int = typer.Option(
        50,
        "--display-limit",
        help="Maximum computed rows to print after storing all scores.",
    ),
) -> None:
    """Compute local embedding-based similarity scores for paper pairs."""

    if table_id is not None and paper_id:
        raise typer.BadParameter("Use either --table-id or repeated --paper-id, not both.")
    results = _library().compute_similarities(paper_ids=paper_id, table_id=table_id)
    _print_similarity_results(results, display_limit=display_limit)


@similarity_app.command("list")
def list_similarity(
    paper_id: int = typer.Argument(..., help="Paper id to find related papers for."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum similar papers to show."),
    min_score: float | None = typer.Option(None, "--min-score", help="Minimum score to show."),
) -> None:
    """List papers with precomputed similarity scores for one paper."""

    _print_similar_papers(
        paper_id,
        _library().similar_papers(paper_id, limit=limit, min_score=min_score),
    )


@app.command()
def ask(
    question: str = typer.Argument(..., help="Standalone question. No chat history is used."),
    paper_id: list[int] | None = typer.Option(
        None,
        "--paper-id",
        help="Paper id to include. Can be supplied multiple times.",
    ),
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Ask over all papers in this table. Ignored when --paper-id is supplied.",
    ),
    top_k: int | None = typer.Option(
        None,
        "--top-k",
        help="Chunks to retrieve per paper. Defaults to CATENA_TOP_K.",
    ),
) -> None:
    """Ask a one-off question over selected paper(s), without storing chat history."""

    if not paper_id and table_id is None:
        raise typer.BadParameter("Provide at least one --paper-id or a --table-id.")
    answer = asyncio.run(
        _library().ask(
            question,
            paper_ids=paper_id,
            table_id=table_id,
            top_k=top_k,
        )
    )
    _print_one_off_answer(answer)


@app.command()
def run(
    table_id: int | None = typer.Option(None, "--table-id", help="Only run one table."),
    column_id: int | None = typer.Option(None, "--column-id", help="Only run one column."),
    paper_id: int | None = typer.Option(None, "--paper-id", help="Only run one paper."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum queued cells to run."),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Also rerun failed cells."),
) -> None:
    """Run queued extraction cells. Without --table-id, runs queued cells across all tables."""

    results = asyncio.run(
        _library().run_pending(
            table_id=table_id,
            column_id=column_id,
            paper_id=paper_id,
            limit=limit,
            retry_failed=retry_failed,
        )
    )
    _print_run_results(results)


@app.command()
def table(
    table_id: int | None = typer.Option(None, "--table-id", help="Table id. Defaults to Default."),
) -> None:
    """Show one extraction matrix."""

    library = _library()
    resolved_table_id = _resolve_table_id(library, table_id)
    extraction_table, columns, rows = library.table_rows(resolved_table_id)
    rich_table = Table(title=f"catena: {extraction_table.name} ({resolved_table_id})")
    rich_table.add_column("ID", justify="right", no_wrap=True)
    rich_table.add_column("Paper")
    rich_table.add_column("Parse", no_wrap=True)
    rich_table.add_column("Index", no_wrap=True)
    for column in columns:
        rich_table.add_column(column.name)
    for row in rows:
        rich_table.add_row(
            row["id"],
            truncate(row["title"], 50),
            row["parse_status"],
            row["index_status"],
            *[truncate(row.get(column.name, ""), 50) for column in columns],
        )
    console.print(rich_table)


def _print_table(table: ExtractionTable) -> None:
    console.print(f"[green]Table[/green] {table.id}: {table.name}")
    if table.description:
        console.print(f"  {table.description}")


def _print_membership(membership: TablePaper) -> None:
    console.print(
        f"[green]Attached[/green] paper {membership.paper_id} "
        f"to table {membership.table_id} ({membership.status})"
    )


def _print_paper(paper: Paper) -> None:
    console.print(f"[green]Paper[/green] {paper.id}: {paper.title}")
    if paper.year:
        console.print(f"  year: {paper.year}")
    if paper.venue:
        console.print(f"  venue: {paper.venue}")
    if paper.citation_count is not None:
        console.print(f"  citations: {paper.citation_count}")
    if paper.doi:
        console.print(f"  doi: {paper.doi}")
    console.print(f"  parse: {paper.parse_status}")
    console.print(f"  index: {paper.index_status}")
    if paper.id is not None:
        tags = _library().paper_tag_names(paper.id)
        if tags:
            console.print(f"  tags: {', '.join(tags)}")
    if paper.parse_error:
        console.print(f"  [red]error:[/red] {paper.parse_error}")


def _print_papers(papers: list[Paper], *, title: str) -> None:
    library = _library()
    rich_table = Table(title=title)
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Title")
    rich_table.add_column("Year", justify="right")
    rich_table.add_column("Cites", justify="right")
    rich_table.add_column("Venue")
    rich_table.add_column("Tags")
    rich_table.add_column("Parse")
    rich_table.add_column("Index")
    for paper in papers:
        tags = library.paper_tag_names(paper.id or 0) if paper.id is not None else []
        rich_table.add_row(
            str(paper.id),
            truncate(paper.title, 60),
            str(paper.year or ""),
            str(paper.citation_count if paper.citation_count is not None else ""),
            truncate(paper.venue or "", 30),
            truncate(", ".join(tags), 30),
            paper.parse_status,
            paper.index_status,
        )
    console.print(rich_table)


def _print_similarity_results(results: list[PaperSimilarity], *, display_limit: int) -> None:
    if not results:
        console.print(
            "No similarities computed. Add/index at least two papers with chunk embeddings first."
        )
        return
    rich_table = Table(title="Computed paper similarities")
    rich_table.add_column("Paper A", justify="right")
    rich_table.add_column("Paper B", justify="right")
    rich_table.add_column("Score", justify="right")
    rich_table.add_column("Cosine", justify="right")
    rich_table.add_column("Algorithm")
    for row in results[: max(0, display_limit)]:
        rich_table.add_row(
            str(row.paper_id_a),
            str(row.paper_id_b),
            f"{row.score:.3f}",
            f"{row.cosine_similarity:.3f}",
            row.algorithm,
        )
    console.print(rich_table)
    if len(results) > display_limit:
        console.print(f"Stored {len(results)} rows; showing top {display_limit}.")


def _print_similar_papers(paper_id: int, results: list[SimilarPaper]) -> None:
    items = list(results)
    if not items:
        console.print(
            f"No precomputed similarities found for paper {paper_id}. "
            "Run `catena similarity compute` first."
        )
        return
    rich_table = Table(title=f"Similar papers for {paper_id}")
    rich_table.add_column("Paper", justify="right")
    rich_table.add_column("Title")
    rich_table.add_column("Score", justify="right")
    rich_table.add_column("Cosine", justify="right")
    rich_table.add_column("Algorithm")
    for item in items:
        rich_table.add_row(
            str(item.paper.id),
            truncate(item.paper.title, 70),
            f"{item.similarity.score:.3f}",
            f"{item.similarity.cosine_similarity:.3f}",
            item.similarity.algorithm,
        )
    console.print(rich_table)


def _print_one_off_answer(answer: OneOffAnswer) -> None:
    console.print("[bold]Answer[/bold]")
    console.print(answer.answer)
    if answer.confidence:
        console.print(f"[dim]confidence:[/dim] {answer.confidence}")
    if answer.rationale:
        console.print(f"[dim]rationale:[/dim] {answer.rationale}")
    if answer.retrieved_chunk_ids:
        console.print(f"[dim]retrieved chunks:[/dim] {answer.retrieved_chunk_ids}")

    if answer.evidence:
        rich_table = Table(title="Evidence")
        rich_table.add_column("Paper", justify="right")
        rich_table.add_column("Page", justify="right")
        rich_table.add_column("Chunk", justify="right")
        rich_table.add_column("Quote")
        for item in answer.evidence:
            rich_table.add_row(
                str(item.get("paper_id") or ""),
                str(item.get("page") or ""),
                str(item.get("chunk_id") or ""),
                truncate(str(item.get("quote") or ""), 110),
            )
        console.print(rich_table)


def _print_run_results(results: list[ExtractionCell]) -> None:
    if not results:
        console.print("No queued cells found.")
        return
    rich_table = Table(title="Extraction results")
    rich_table.add_column("Cell", justify="right")
    rich_table.add_column("Table", justify="right")
    rich_table.add_column("Paper", justify="right")
    rich_table.add_column("Column", justify="right")
    rich_table.add_column("Status")
    rich_table.add_column("Answer")
    for cell in results:
        rich_table.add_row(
            str(cell.id),
            str(cell.table_id),
            str(cell.paper_id),
            str(cell.column_id),
            cell.status,
            truncate(cell.answer_text or cell.error or "", 80),
        )
    console.print(rich_table)
