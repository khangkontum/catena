from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from catena.config import (
    Settings,
    config_search_paths,
    default_config_path,
    default_skills_dir,
    find_config_file,
    load_default_config_template,
    load_skill_template,
)
from catena.db import show_db_current, show_db_history, upgrade_db
from catena.filters import PaperFilter
from catena.library import CatenaLibrary, IngestProgress, IngestProgressCallback
from catena.models import (
    ExtractionCell,
    ExtractionColumn,
    ExtractionTable,
    Paper,
    Status,
    TablePaper,
    Tag,
)
from catena.qa import OneOffAnswer
from catena.search import SearchResult
from catena.util import truncate

console = Console()
stderr_console = Console(stderr=True)
app = typer.Typer(help="Local evidence-backed paper extraction tables.")
config_app = typer.Typer(help="Manage catena configuration.")
skill_app = typer.Typer(help="Manage the catena agent skill.")
tables_app = typer.Typer(help="Manage extraction tables.")
papers_app = typer.Typer(help="Manage global papers.")
tags_app = typer.Typer(help="Manage paper tags.")
columns_app = typer.Typer(help="Manage extraction columns.")
db_app = typer.Typer(help="Manage Alembic database migrations.")
app.add_typer(tables_app, name="tables")
app.add_typer(papers_app, name="papers")
app.add_typer(tags_app, name="tags")
app.add_typer(columns_app, name="columns")
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")
app.add_typer(skill_app, name="skill")

_json_output = False


@app.callback()
def main(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON instead of human-readable output where supported.",
    ),
) -> None:
    """Local evidence-backed paper extraction tables."""

    global _json_output
    _json_output = json_output


def _library() -> CatenaLibrary:
    return CatenaLibrary(Settings.from_env())


def _resolve_table_id(library: CatenaLibrary, table_id: int | None) -> int:
    return table_id if table_id is not None else library.default_table_id()


@contextmanager
def _ingest_progress() -> Iterator[IngestProgressCallback]:
    if _json_output:
        yield _emit_ingest_progress_stderr
        return

    task_id: TaskID | None = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        def report(event: IngestProgress) -> None:
            nonlocal task_id
            total = event.total or 0
            if task_id is None:
                task_id = progress.add_task(event.message, total=total)
            if event.total is not None:
                progress.update(task_id, total=event.total)
            completed = (
                event.current if event.current is not None else progress.tasks[task_id].completed
            )
            progress.update(task_id, completed=completed, description=event.message)
            if event.error:
                progress.console.print(f"[red]{event.message}:[/red] {event.error}")

        yield report


def _emit_ingest_progress_stderr(event: IngestProgress) -> None:
    parts = [f"step={event.step}"]
    if event.current is not None and event.total is not None:
        parts.append(f"progress={event.current}/{event.total}")
    elif event.total is not None:
        parts.append(f"total={event.total}")
    if event.paper_id is not None:
        parts.append(f"paper_id={event.paper_id}")
    parts.append(event.message)
    if event.error:
        parts.append(f"error={event.error}")
    stderr_console.print("[catena] " + " ".join(parts), markup=False)


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
    if _json_output:
        _emit_ok(data_dir=str(library.settings.data_dir))
        return
    console.print(f"[green]Initialized[/green] {library.settings.data_dir}")


@config_app.callback(invoke_without_command=True)
def config(
    ctx: typer.Context,
) -> None:
    """Show resolved local paths, gateway readiness, and config file source.

    With no subcommand, prints the resolved configuration. Run
    `catena config init` to write a starter config file.
    """

    if ctx.invoked_subcommand is not None:
        return
    settings = Settings.from_env()
    config_path = find_config_file()
    item = _settings_dict(settings)
    item["config_path"] = str(config_path) if config_path else None
    item["config_search_paths"] = [str(p) for p in config_search_paths()]
    if _json_output:
        _emit_item(item)
        return

    table = Table(title="catena config")
    table.add_column("Key")
    table.add_column("Value")
    for key, value in item.items():
        if key == "config_search_paths":
            table.add_row(key, "\n".join(["- " + _display_value(v) for v in value] or [""]))
        else:
            table.add_row(key, _display_value(value))
    console.print(table)


@config_app.command("init")
def config_init(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Target file. Defaults to the first XDG config search path.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing config file.",
    ),
) -> None:
    """Write a starter config file to the default location.

    Writes the packaged template to ~/.config/catena/config.toml (respecting
    $XDG_CONFIG_HOME) unless --path is given. Refuses to overwrite an existing
    file unless --force is passed.
    """

    target = path if path is not None else default_config_path()
    if target.exists() and not force:
        msg = f"{target} already exists; pass --force to overwrite."
        if _json_output:
            _emit_json({"ok": False, "error": msg, "path": str(target)})
        else:
            console.print(f"[red]error:[/red] {msg}")
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(load_default_config_template(), encoding="utf-8")
    if _json_output:
        _emit_ok(path=str(target))
        return
    console.print(f"[green]Wrote[/green] {target}")


@skill_app.command("install")
def skill_install(
    dir: Path | None = typer.Option(
        None,
        "--dir",
        "-d",
        help="Skills root directory. Defaults to ~/.agents/skills ($CATENA_SKILLS_DIR).",
    ),
    name: str = typer.Option(
        "catena",
        "--name",
        "-n",
        help="Skill folder name (default: catena).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing SKILL.md.",
    ),
) -> None:
    """Install the catena agent skill (writes SKILL.md).

    Writes to <dir>/<name>/SKILL.md. Defaults to ~/.agents/skills/catena/SKILL.md so any
    agent scanning that root picks it up. Refuses to overwrite an existing file unless
    --force is passed.
    """

    root = dir if dir is not None else default_skills_dir()
    target = root / name / "SKILL.md"
    if target.exists() and not force:
        msg = f"{target} already exists; pass --force to overwrite."
        if _json_output:
            _emit_json({"ok": False, "error": msg, "path": str(target)})
        else:
            console.print(f"[red]error:[/red] {msg}")
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(load_skill_template(), encoding="utf-8")
    if _json_output:
        _emit_ok(path=str(target), name=name)
        return
    console.print(f"[green]Installed[/green] skill `{name}` -> {target}")


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head", help="Alembic revision.")) -> None:
    """Run Alembic migrations."""

    library = _library()
    upgrade_db(library.engine, revision)
    if _json_output:
        _emit_ok(revision=revision)
        return
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
    if _json_output:
        _emit_ok(item=_table_dict(table))
        return
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
    if _json_output:
        _emit_ok(
            item=_table_dict(table),
            attached_papers=[_paper_dict(paper) for paper in papers],
            attached_count=len(papers),
        )
        return
    _print_table(table)
    console.print(f"  attached papers: {len(papers)}")


@tables_app.command("refresh")
def refresh_table(
    table_id: int = typer.Argument(..., help="Table id created from a saved filter."),
    prune: bool = typer.Option(False, "--prune", help="Remove papers that no longer match."),
) -> None:
    """Refresh a filtered table after adding tags or metadata."""

    papers = _library().refresh_table_from_filter(table_id, prune=prune)
    if _json_output:
        _emit_ok(
            table_id=table_id,
            items=[_paper_dict(paper) for paper in papers],
            count=len(papers),
        )
        return
    console.print(f"[green]Refreshed[/green] table {table_id}; matching papers: {len(papers)}")


@tables_app.command("list")
def list_tables() -> None:
    """List extraction tables."""

    tables = _library().tables()
    if _json_output:
        _emit_items([_table_dict(table) for table in tables])
        return

    rich_table = Table(title="Extraction tables")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Description")
    rich_table.add_column("Filtered")
    for table in tables:
        rich_table.add_row(
            str(table.id),
            table.name,
            table.description or "",
            "yes" if table.source_filter_json else "no",
        )
    console.print(rich_table)


@tables_app.command("show")
def show_table(
    table_id: int | None = typer.Option(None, "--table-id", help="Table id. Defaults to Default."),
) -> None:
    """Show one extraction matrix."""

    library = _library()
    resolved_table_id = _resolve_table_id(library, table_id)
    extraction_table, columns, rows = library.table_rows(resolved_table_id)
    if _json_output:
        _emit_json(_table_matrix_dict(extraction_table, columns, rows))
        return

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


@tables_app.command("add-paper")
def add_paper_to_table(
    table_id: int = typer.Argument(..., help="Extraction table id."),
    paper_id: int = typer.Argument(..., help="Global paper id."),
) -> None:
    """Attach an existing global paper to a table and queue that table's columns."""

    membership = _library().add_paper_to_table(table_id, paper_id)
    if _json_output:
        _emit_ok(item=_membership_dict(membership))
        return
    _print_membership(membership)


@tables_app.command("papers")
def list_table_papers(
    table_id: int | None = typer.Option(None, "--table-id", help="Table id. Defaults to Default."),
) -> None:
    """List papers in one extraction table."""

    library = _library()
    resolved_table_id = _resolve_table_id(library, table_id)
    papers = library.papers(table_id=resolved_table_id)
    if _json_output:
        _emit_items(_paper_dicts_with_tags(library, papers), table_id=resolved_table_id)
        return
    _print_papers(papers, title=f"Papers in table {resolved_table_id}")


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
    if _json_output:
        _emit_ok(
            item=_paper_dict(paper, tags=_tags_for_paper(library, paper)),
            table_id=resolved_table_id,
        )
        return
    _print_paper(paper)
    if resolved_table_id is not None:
        console.print(f"  table: {resolved_table_id}")


@papers_app.command("add-dir")
def add_dir(
    directory: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Folder of PDFs to import. Recurses by default.",
    ),
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Attach to this existing table instead of deriving one from the folder.",
    ),
    table_name: str | None = typer.Option(
        None,
        "--table-name",
        help="Override the derived table name (derived from the last two path segments).",
    ),
    no_table: bool = typer.Option(
        False,
        "--no-table",
        help="Only register papers globally; attach to no table.",
    ),
    recursive: bool = typer.Option(
        True,
        "--recursive/--no-recursive",
        help="Recurse into subfolders. Default: on.",
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help="After ingest, run queued extraction cells in the target table.",
    ),
    register_only: bool = typer.Option(
        False,
        "--async",
        help="Register papers only; exit before parsing/indexing. Poll with "
        "`papers import-status`. Incompatible with --run.",
    ),
) -> None:
    """Import a folder of PDFs into one table bound to the folder path.

    By default the table is derived from the imported directory's resolved absolute
    path: the same folder always maps to the same table, so re-running the import is
    idempotent (papers dedup by content hash; the table and its memberships are reused).
    Default behavior registers, parses, and indexes in one blocking call. Use --async
    to register only and defer parsing to `papers ingest` (run detached, poll status).
    """

    if register_only and run:
        raise typer.BadParameter(
            "--async cannot be combined with --run: nothing is parsed yet. "
            "Run `catena run --table-id N` after `catena papers ingest` completes."
        )
    if no_table and (table_id is not None or table_name is not None):
        raise typer.BadParameter("--no-table cannot be combined with --table-id or --table-name.")

    resolved_dir = directory.expanduser().resolve()
    pdfs = _collect_pdfs(resolved_dir, recursive=recursive)
    if not pdfs:
        raise typer.BadParameter(f"No PDF files found in {resolved_dir}")

    library = _library()

    source_path: str | None = None
    derived_name: str | None = None
    if no_table:
        resolved_table_id: int | None = None
    elif table_id is not None:
        resolved_table_id = table_id
    else:
        derived_name = table_name or _folder_table_name(resolved_dir)
        table = library.get_or_create_table_for_path(
            resolved_dir,
            name=derived_name,
            description=f"Imported from {resolved_dir}",
        )
        resolved_table_id = table.id
        source_path = table.source_path
        derived_name = table.name

    registered = library.register_pdfs(pdfs, table_id=resolved_table_id)
    new_papers = [item for item in registered if item.is_new]
    existing_papers = [item for item in registered if not item.is_new]

    if register_only:
        next_hint = (
            f"catena papers ingest --table-id {resolved_table_id}"
            if resolved_table_id is not None
            else "catena papers ingest --all"
        )
        if _json_output:
            _emit_ok(
                table_id=resolved_table_id,
                table_name=derived_name,
                source_path=source_path,
                registered=[_registered_dict(item) for item in registered],
                queued=len(new_papers),
                existing=len(existing_papers),
                ingested=[],
                next=next_hint,
            )
            return
        console.print(f"[green]Registered[/green] {len(registered)} PDF(s)")
        if resolved_table_id is not None:
            console.print(
                f"  table: {resolved_table_id} ({derived_name})"
                + (f"  source: {source_path}" if source_path else "")
            )
        console.print(f"  new: {len(new_papers)}  existing: {len(existing_papers)}")
        console.print(f"  next: {next_hint}")
        return

    ingest_results: list[Any] = []
    if new_papers:
        with _ingest_progress() as progress:
            ingest_results = asyncio.run(
                library.ingest_papers(
                    paper_ids=[item.paper_id for item in new_papers],
                    progress=progress,
                )
            )
        if not _json_output:
            for result in ingest_results:
                mark = (
                    "[green]parsed[/green]"
                    if result.parse_status == Status.PARSED
                    else "[red]failed[/red]"
                )
                console.print(f"  paper {result.paper_id}: {mark}")
                if result.error:
                    console.print(f"    [red]error:[/red] {result.error}")

    run_results: list[ExtractionCell] = []
    if run and resolved_table_id is not None:
        run_results = asyncio.run(library.run_pending(table_id=resolved_table_id))

    if _json_output:
        payload: dict[str, Any] = {
            "table_id": resolved_table_id,
            "table_name": derived_name,
            "source_path": source_path,
            "registered": [_registered_dict(item) for item in registered],
            "queued": len(new_papers),
            "existing": len(existing_papers),
            "ingested": [_ingest_result_dict(result) for result in ingest_results],
        }
        if run and resolved_table_id is not None:
            payload["run_results"] = [_cell_dict(cell) for cell in run_results]
            payload["run_count"] = len(run_results)
        _emit_ok(**payload)
        return

    console.print(
        f"[green]Imported[/green] {len(registered)} PDF(s) into table {resolved_table_id}"
        if resolved_table_id is not None
        else f"[green]Imported[/green] {len(registered)} PDF(s) into global library"
    )
    if run and run_results:
        _print_run_results(run_results)


@papers_app.command("ingest")
def ingest(
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Ingest QUEUED papers in this table. Required unless --all.",
    ),
    all_papers: bool = typer.Option(
        False,
        "--all",
        help="Ingest all QUEUED papers globally. Use either --table-id or --all.",
    ),
    retry_failed: bool = typer.Option(
        False,
        "--retry-failed",
        help="Also re-ingest FAILED papers.",
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help="After ingest, run queued extraction cells in the table.",
    ),
) -> None:
    """Parse and index previously registered (--async) papers.

    Runs a single batched Docling pass over the QUEUED papers in scope (plus FAILED if
    --retry-failed). Designed to be run detached (e.g. via zmx) while an agent polls
    `papers import-status`. By default scoped to one table; pass --all for global scope.
    """

    if table_id is None and not all_papers:
        raise typer.BadParameter("Provide --table-id or --all.")
    if all_papers and table_id is not None:
        raise typer.BadParameter("Use either --table-id or --all, not both.")

    library = _library()
    with _ingest_progress() as progress:
        results = asyncio.run(
            library.ingest_papers(
                table_id=table_id,
                retry_failed=retry_failed,
                progress=progress,
            )
        )
    run_results: list[ExtractionCell] = []
    if run and table_id is not None:
        run_results = asyncio.run(library.run_pending(table_id=table_id))

    if _json_output:
        payload: dict[str, Any] = {
            "items": [_ingest_result_dict(result) for result in results],
            "count": len(results),
            "table_id": table_id,
        }
        if run and table_id is not None:
            payload["run_results"] = [_cell_dict(cell) for cell in run_results]
            payload["run_count"] = len(run_results)
        _emit_ok(**payload)
        return

    if not results:
        console.print("No queued papers to ingest.")
        return
    for result in results:
        mark = (
            "[green]parsed[/green]"
            if result.parse_status == Status.PARSED
            else "[red]failed[/red]"
        )
        console.print(f"  paper {result.paper_id}: {mark}")
        if result.error:
            console.print(f"    [red]error:[/red] {result.error}")
    if run and run_results:
        _print_run_results(run_results)


@papers_app.command("import-status")
def import_status(
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Scope to a table. Defaults to all papers.",
    ),
) -> None:
    """Show parse/index status counts and failures for registered papers.

    Read-only poll for the async import flow: after `papers add-dir --async`, run this
    (optionally scoped to the table) to see how many papers are queued/running/parsed/
    indexed/failed, plus the error for each failed paper.
    """

    library = _library()
    papers = library.papers(table_id=table_id)

    parse_counts: dict[str, int] = {}
    index_counts: dict[str, int] = {}
    failed: list[dict[str, Any]] = []
    for paper in papers:
        parse_counts[paper.parse_status] = parse_counts.get(paper.parse_status, 0) + 1
        index_counts[paper.index_status] = index_counts.get(paper.index_status, 0) + 1
        if (
            paper.parse_status == Status.FAILED
            or paper.index_status == Status.FAILED
        ) and paper.id is not None:
            failed.append(
                {
                    "paper_id": paper.id,
                    "title": paper.title,
                    "parse_status": paper.parse_status,
                    "index_status": paper.index_status,
                    "error": paper.parse_error,
                }
            )

    if _json_output:
        _emit_ok(
            table_id=table_id,
            count=len(papers),
            parse_status=parse_counts,
            index_status=index_counts,
            failed=failed,
            items=[
                {
                    "paper_id": paper.id,
                    "title": paper.title,
                    "parse_status": paper.parse_status,
                    "index_status": paper.index_status,
                }
                for paper in papers
            ],
        )
        return

    rich_table = Table(title=f"Import status{f' (table {table_id})' if table_id else ''}")
    rich_table.add_column("Paper", justify="right")
    rich_table.add_column("Title")
    rich_table.add_column("Parse")
    rich_table.add_column("Index")
    for paper in papers:
        rich_table.add_row(
            str(paper.id),
            truncate(paper.title, 60),
            paper.parse_status,
            paper.index_status,
        )
    console.print(rich_table)
    if parse_counts.get(Status.QUEUED, 0) or parse_counts.get(Status.RUNNING, 0):
        console.print(
            f"  queued: {parse_counts.get(Status.QUEUED, 0)}  "
            f"running: {parse_counts.get(Status.RUNNING, 0)}  "
            f"parsed: {parse_counts.get(Status.PARSED, 0)}  "
            f"indexed: {parse_counts.get(Status.INDEXED, 0)}  "
            f"failed: {parse_counts.get(Status.FAILED, 0)}"
        )
    if failed:
        console.print("[red]Failed papers:[/red]")
        for item in failed:
            console.print(
                f"  paper {item['paper_id']}: {item['parse_status']}/"
                f"{item['index_status']} - {item['error']}"
            )


@papers_app.command("list")
def list_papers(
    table_id: int | None = typer.Option(None, "--table-id", help="Only list papers in a table."),
) -> None:
    """List global papers, or papers in a specific table."""

    library = _library()
    papers = library.papers(table_id=table_id)
    if _json_output:
        _emit_items(_paper_dicts_with_tags(library, papers), table_id=table_id)
        return
    title = "Papers" if table_id is None else f"Papers in table {table_id}"
    _print_papers(papers, title=title)


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

    library = _library()
    paper = library.set_paper_metadata(
        paper_id,
        year=year,
        venue=venue,
        citation_count=citations,
        doi=doi,
        abstract=abstract,
    )
    if _json_output:
        _emit_ok(item=_paper_dict(paper, tags=_tags_for_paper(library, paper)))
        return
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

    papers: list[Paper] = []
    for resolved_paper_id in paper_ids:
        if resolved_paper_id is None:
            continue
        paper = asyncio.run(library.enrich_paper(resolved_paper_id))
        papers.append(paper)
        if not _json_output:
            _print_paper(paper)
    if _json_output:
        _emit_ok(items=_paper_dicts_with_tags(library, papers), count=len(papers))


@tags_app.command("create")
def create_tag(
    name: str = typer.Argument(...),
    color: str | None = typer.Option(None, "--color"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Create or update a tag."""

    tag = _library().create_tag(name, color=color, description=description)
    if _json_output:
        _emit_ok(item=_tag_dict(tag))
        return
    console.print(f"[green]Tag[/green] {tag.id}: {tag.name}")


@tags_app.command("list")
def list_tags() -> None:
    """List tags."""

    tags = _library().tags()
    if _json_output:
        _emit_items([_tag_dict(tag) for tag in tags])
        return

    rich_table = Table(title="Tags")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Color")
    rich_table.add_column("Description")
    for tag in tags:
        rich_table.add_row(str(tag.id), tag.name, tag.color or "", tag.description or "")
    console.print(rich_table)


@tags_app.command("add")
def add_tag_to_paper(
    paper_id: int = typer.Argument(...),
    tags: list[str] = typer.Argument(..., help="One or more tag names."),
) -> None:
    """Add tag(s) to a paper."""

    library = _library()
    tagged: list[Tag] = []
    for tag_name in tags:
        tag = library.tag_paper(paper_id, tag_name)
        tagged.append(tag)
        if not _json_output:
            console.print(f"[green]Tagged[/green] paper {paper_id} with {tag.name}")
    if _json_output:
        _emit_ok(paper_id=paper_id, items=[_tag_dict(tag) for tag in tagged], count=len(tagged))


@tags_app.command("remove")
def remove_tag_from_paper(
    paper_id: int = typer.Argument(...),
    tag: str = typer.Argument(...),
) -> None:
    """Remove a tag from a paper."""

    _library().untag_paper(paper_id, tag)
    if _json_output:
        _emit_ok(paper_id=paper_id, tag=tag)
        return
    console.print(f"[green]Removed[/green] tag {tag} from paper {paper_id}")


@tags_app.command("papers")
def papers_by_tag(
    tag: list[str] = typer.Option(..., "--tag", help="Required tag. Repeat for AND."),
) -> None:
    """List papers that have all provided tags."""

    library = _library()
    paper_filter = PaperFilter(tags_all=tag)
    papers = library.filter_papers(paper_filter)
    if _json_output:
        _emit_items(_paper_dicts_with_tags(library, papers), tags=tag)
        return
    _print_papers(papers, title="Tagged papers")


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
    results: list[ExtractionCell] = []
    if run:
        results = asyncio.run(library.run_pending(table_id=resolved_table_id, column_id=column.id))
    if _json_output:
        payload: dict[str, Any] = {"item": _column_dict(column), "table_id": resolved_table_id}
        if run:
            payload["run_results"] = [_cell_dict(cell) for cell in results]
            payload["run_count"] = len(results)
        _emit_ok(**payload)
        return

    console.print(
        f"[green]Created column[/green] {column.id}: {column.name} "
        f"in table {resolved_table_id}"
    )
    if run:
        _print_run_results(results)


@columns_app.command("list")
def list_columns(
    table_id: int | None = typer.Option(None, "--table-id", help="Only list columns in a table."),
) -> None:
    """List extraction columns."""

    columns = _library().columns(table_id=table_id)
    if _json_output:
        _emit_items([_column_dict(column) for column in columns], table_id=table_id)
        return

    rich_table = Table(title="Extraction columns")
    rich_table.add_column("ID", justify="right")
    rich_table.add_column("Table", justify="right")
    rich_table.add_column("Name")
    rich_table.add_column("Prompt")
    rich_table.add_column("Top K")
    for column in columns:
        rich_table.add_row(
            str(column.id),
            str(column.table_id),
            column.name,
            truncate(column.prompt, 90),
            str(column.top_k or "default"),
        )
    console.print(rich_table)


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
        help="Retrieval budget. Per paper for synthesis, global for fast mode.",
    ),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="Ask mode: auto, fast, synthesis, or matrix.",
    ),
    max_context_chars: int | None = typer.Option(
        None,
        "--max-context-chars",
        help="Maximum context characters sent to each answer prompt.",
    ),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        help="Papers per map call in synthesis mode. Defaults to 1.",
    ),
) -> None:
    """Ask a one-off question over selected paper(s), without storing chat history."""

    if not paper_id and table_id is None:
        raise typer.BadParameter("Provide at least one --paper-id or a --table-id.")
    if mode not in {"auto", "fast", "synthesis", "matrix"}:
        raise typer.BadParameter("--mode must be one of: auto, fast, synthesis, matrix")
    answer = asyncio.run(
        _library().ask(
            question,
            paper_ids=paper_id,
            table_id=table_id,
            top_k=top_k,
            mode=mode,  # type: ignore[arg-type]
            max_context_chars=max_context_chars,
            batch_size=batch_size,
        )
    )
    if _json_output:
        _emit_item(_answer_dict(answer))
        return
    _print_one_off_answer(answer)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query, paper title, or exact text."),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="Search mode: auto, hybrid, semantic, text, title, or exact.",
    ),
    paper_id: list[int] | None = typer.Option(
        None,
        "--paper-id",
        help="Paper id to include. Can be supplied multiple times.",
    ),
    table_id: int | None = typer.Option(
        None,
        "--table-id",
        help="Search papers in this table. Ignored when --paper-id is supplied.",
    ),
    top_k: int | None = typer.Option(
        None,
        "--top-k",
        help="Maximum results. Defaults to CATENA_TOP_K.",
    ),
) -> None:
    """Search the local paper library by title, exact text, FTS, semantic, or hybrid ranking."""

    if mode not in {"auto", "hybrid", "semantic", "text", "title", "exact"}:
        raise typer.BadParameter(
            "--mode must be one of: auto, hybrid, semantic, text, title, exact"
        )
    if not query.strip():
        raise typer.BadParameter("Search query cannot be empty.")
    try:
        results = asyncio.run(
            _library().search(
                query,
                mode=mode,  # type: ignore[arg-type]
                paper_ids=paper_id,
                table_id=table_id,
                top_k=top_k,
            )
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if _json_output:
        _emit_items([_search_result_dict(result) for result in results], query=query, mode=mode)
        return
    _print_search_results(results)


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
    if _json_output:
        message = None if results else "No queued cells found."
        _emit_items([_cell_dict(cell) for cell in results], message=message)
        return
    _print_run_results(results)


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


def _print_one_off_answer(answer: OneOffAnswer) -> None:
    console.print("[bold]Answer[/bold]")
    console.print(answer.answer)
    console.print(f"[dim]mode:[/dim] {answer.mode}")
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


def _print_search_results(results: list[SearchResult]) -> None:
    if not results:
        console.print("No search results found.")
        return
    rich_table = Table(title="Search results")
    rich_table.add_column("Score", justify="right")
    rich_table.add_column("Kind")
    rich_table.add_column("Paper", justify="right")
    rich_table.add_column("Title")
    rich_table.add_column("Page", justify="right")
    rich_table.add_column("Heading")
    rich_table.add_column("Snippet")
    for result in results:
        rich_table.add_row(
            f"{result.score:.4f}",
            result.kind,
            str(result.paper_id),
            truncate(result.paper_title, 42),
            str(result.page_start or ""),
            truncate(result.heading or "", 24),
            truncate(result.snippet, 80),
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


def _emit_json(payload: dict[str, Any]) -> None:
    console.print_json(data=payload)


def _emit_items(
    items: list[dict[str, Any]],
    *,
    message: str | None = None,
    **extra: Any,
) -> None:
    payload = {"items": items, "count": len(items), **extra}
    if message is not None:
        payload["message"] = message
    _emit_json(payload)


def _emit_item(item: dict[str, Any], **extra: Any) -> None:
    _emit_json({"item": item, **extra})


def _emit_ok(**extra: Any) -> None:
    _emit_json({"ok": True, **extra})


def _settings_dict(settings: Settings) -> dict[str, Any]:
    return {
        "data_dir": str(settings.data_dir),
        "sqlite": str(settings.sqlite_path),
        "lancedb": str(settings.lancedb_uri),
        "gateway_ready": settings.gateway_ready,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "embedding_batch_size": settings.embedding_batch_size,
        "top_k": settings.top_k,
        "llm_temperature": settings.llm_temperature,
        "cell_concurrency": settings.cell_concurrency,
    }


def _model_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        dumped = item.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    raise TypeError(f"Cannot serialize {type(item).__name__}")


def _table_dict(table: ExtractionTable) -> dict[str, Any]:
    return _model_dict(table)


def _paper_dict(paper: Paper, *, tags: list[str] | None = None) -> dict[str, Any]:
    item = _model_dict(paper)
    if tags is not None:
        item["tags"] = tags
    return item


def _paper_dicts_with_tags(library: CatenaLibrary, papers: list[Paper]) -> list[dict[str, Any]]:
    return [_paper_dict(paper, tags=_tags_for_paper(library, paper)) for paper in papers]


def _tags_for_paper(library: CatenaLibrary, paper: Paper) -> list[str]:
    if paper.id is None:
        return []
    return library.paper_tag_names(paper.id)


def _tag_dict(tag: Tag) -> dict[str, Any]:
    return _model_dict(tag)


def _column_dict(column: ExtractionColumn) -> dict[str, Any]:
    return _model_dict(column)


def _membership_dict(membership: TablePaper) -> dict[str, Any]:
    return _model_dict(membership)


def _cell_dict(cell: ExtractionCell) -> dict[str, Any]:
    return _model_dict(cell)


def _answer_dict(answer: OneOffAnswer) -> dict[str, Any]:
    return {
        "question": answer.question,
        "paper_ids": answer.paper_ids,
        "mode": answer.mode,
        "answer": answer.answer,
        "evidence": answer.evidence,
        "confidence": answer.confidence,
        "rationale": answer.rationale,
        "raw": answer.raw,
        "retrieved_chunk_ids": answer.retrieved_chunk_ids,
    }


def _search_result_dict(result: SearchResult) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "paper_title": result.paper_title,
        "score": result.score,
        "kind": result.kind,
        "snippet": result.snippet,
        "chunk_id": result.chunk_id,
        "chunk_index": result.chunk_index,
        "page_start": result.page_start,
        "page_end": result.page_end,
        "heading": result.heading,
        "component_scores": result.component_scores,
    }


def _table_matrix_dict(
    table: ExtractionTable,
    columns: list[ExtractionColumn],
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "item": _table_dict(table),
        "columns": [_column_dict(column) for column in columns],
        "rows": [
            {
                "paper_id": int(row["id"]),
                "title": row["title"],
                "parse_status": row["parse_status"],
                "index_status": row["index_status"],
                "values": [
                    {
                        "column_id": column.id,
                        "column_name": column.name,
                        "value": row.get(column.name, ""),
                    }
                    for column in columns
                ],
            }
            for row in rows
        ],
    }


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    return str(value)


def _collect_pdfs(directory: Path, *, recursive: bool) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    return sorted(p for p in iterator if p.is_file() and p.suffix.lower() == ".pdf")


def _folder_table_name(directory: Path) -> str:
    """Stable display name from the last two path segments of a resolved folder.

    The durable identity of a folder-import table is its resolved absolute path
    (stored on the table), not this name; this slug only makes `tables list` readable
    and disambiguates folders that share a basename.
    """

    parts = [p for p in directory.parts if p not in ("/", "\\")]
    tail = parts[-2:] if len(parts) >= 2 else parts
    slug = "-".join(tail).strip()
    return slug or directory.name or "import"


def _registered_dict(item: Any) -> dict[str, Any]:
    return {"paper_id": item.paper_id, "title": item.title, "is_new": item.is_new}


def _ingest_result_dict(result: Any) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "parse_status": result.parse_status,
        "index_status": result.index_status,
        "error": result.error,
    }
