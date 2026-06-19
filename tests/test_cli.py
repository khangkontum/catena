import json
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from catena.cli import app
from catena.library import CatenaLibrary
from catena.models import Paper, Status
from catena.parsing import ParsedChunk, ParsedDocument, ParsedPdfResult

runner = CliRunner()


def invoke_json(args: list[str], tmp_path):
    result = runner.invoke(app, ["--json", *args], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _env(tmp_path) -> dict[str, str]:
    return {
        "CATENA_DATA_DIR": str(tmp_path),
        "LLM_GATEWAY_BASE_URL": "",
        "LLM_GATEWAY_API_KEY": "",
        "LLM_MODEL": "",
        "LLM_EMBEDDING_MODEL": "",
    }


def test_config_json_uses_stable_item_envelope(tmp_path):
    payload = invoke_json(["config"], tmp_path)

    assert payload["item"]["data_dir"] == str(tmp_path)
    assert payload["item"]["gateway_ready"] is False
    assert payload["item"]["sqlite"] == str(tmp_path / "catena.sqlite")


def test_table_json_create_list_and_show(tmp_path):
    created = invoke_json(["tables", "create", "Screening", "--description", "Review"], tmp_path)

    assert created["ok"] is True
    assert created["item"]["name"] == "Screening"
    assert created["item"]["description"] == "Review"

    listed = invoke_json(["tables", "list"], tmp_path)
    assert listed["count"] == 2
    assert [item["name"] for item in listed["items"]] == ["Default", "Screening"]

    shown = invoke_json(["tables", "show", "--table-id", str(created["item"]["id"])], tmp_path)
    assert shown["item"]["name"] == "Screening"
    assert shown["columns"] == []
    assert shown["rows"] == []


def test_similarity_and_legacy_table_commands_are_not_public(tmp_path):
    env = _env(tmp_path)

    top_help = runner.invoke(app, ["--help"], env=env)
    assert top_help.exit_code == 0
    assert "similarity" not in top_help.output

    assert runner.invoke(app, ["table"], env=env).exit_code != 0
    assert runner.invoke(app, ["similarity"], env=env).exit_code != 0
    assert runner.invoke(app, ["papers", "similar", "1"], env=env).exit_code != 0


@pytest.fixture
def fake_ingest(monkeypatch):
    """Stub Docling parsing and the gateway-backed indexer so CLI tests run offline."""

    parse_calls: list[list[Path]] = []

    def fake_parse_pdfs(paths: list[Path]) -> list[ParsedPdfResult]:
        parse_calls.append(list(paths))
        return [
            ParsedPdfResult(
                path=path,
                document=ParsedDocument(
                    markdown=f"# {path.stem}",
                    docling_json={"name": path.name},
                    chunks=[
                        ParsedChunk(
                            index=0,
                            text=f"chunk for {path.name}",
                            page_start=1,
                            page_end=1,
                            heading=None,
                            metadata={},
                        )
                    ],
                ),
            )
            for path in paths
        ]

    async def fake_index_paper(self: CatenaLibrary, paper_id: int) -> None:
        with Session(self.engine) as session:
            paper = session.get(Paper, paper_id)
            assert paper is not None
            paper.index_status = Status.INDEXED
            session.add(paper)
            session.commit()

    monkeypatch.setattr("catena.library.parse_pdfs", fake_parse_pdfs)
    monkeypatch.setattr(CatenaLibrary, "index_paper", fake_index_paper)
    return parse_calls


def _write_pdfs(folder: Path, names: list[str]) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(name.encode())
        paths.append(path)
    return paths


def test_add_dir_imports_folder_into_path_bound_table(tmp_path, fake_ingest):
    folder = tmp_path / "cohort"
    _write_pdfs(folder, ["a.pdf", "b.pdf"])
    nested = folder / "sub"
    _write_pdfs(nested, ["c.pdf"])

    payload = invoke_json(["papers", "add-dir", str(folder)], tmp_path)

    assert payload["ok"] is True
    assert payload["queued"] == 3
    assert payload["existing"] == 0
    assert payload["source_path"] == str(folder.resolve())
    assert payload["table_name"]  # derived slug
    table_id = payload["table_id"]

    # one batched Docling pass for the 3 new papers
    assert len(fake_ingest) == 1
    assert len(fake_ingest[0]) == 3
    assert [item["parse_status"] for item in payload["ingested"]] == ["parsed"] * 3
    assert [item["index_status"] for item in payload["ingested"]] == ["indexed"] * 3

    # re-running is idempotent: same table (path identity), all papers reused, no re-parse
    again = invoke_json(["papers", "add-dir", str(folder)], tmp_path)
    assert again["table_id"] == table_id
    assert again["source_path"] == str(folder.resolve())
    assert again["queued"] == 0
    assert again["existing"] == 3
    assert again["ingested"] == []
    assert len(fake_ingest) == 1  # no second parse


def test_add_dir_async_then_ingest_then_import_status(tmp_path, fake_ingest):
    folder = tmp_path / "papers"
    _write_pdfs(folder, ["one.pdf", "two.pdf"])

    registered = invoke_json(["papers", "add-dir", str(folder), "--async"], tmp_path)
    assert registered["queued"] == 2
    assert registered["existing"] == 0
    assert registered["ingested"] == []
    assert "ingest --table-id" in registered["next"]
    table_id = registered["table_id"]
    assert fake_ingest == []  # nothing parsed yet

    status = invoke_json(["papers", "import-status", "--table-id", str(table_id)], tmp_path)
    assert status["parse_status"].get("queued") == 2
    assert status["count"] == 2

    ingested = invoke_json(["papers", "ingest", "--table-id", str(table_id)], tmp_path)
    assert ingested["count"] == 2
    assert [item["parse_status"] for item in ingested["items"]] == ["parsed"] * 2

    final = invoke_json(["papers", "import-status", "--table-id", str(table_id)], tmp_path)
    assert final["index_status"].get("indexed") == 2
    assert final["parse_status"].get("parsed") == 2
    assert final["failed"] == []


def test_add_dir_distinct_folders_get_distinct_tables(tmp_path, fake_ingest):
    one = tmp_path / "alpha"
    two = tmp_path / "beta"
    _write_pdfs(one, ["x.pdf"])
    _write_pdfs(two, ["y.pdf"])

    first = invoke_json(["papers", "add-dir", str(one)], tmp_path)
    second = invoke_json(["papers", "add-dir", str(two)], tmp_path)

    assert first["table_id"] != second["table_id"]
    assert first["source_path"] == str(one.resolve())
    assert second["source_path"] == str(two.resolve())


def test_add_dir_no_recursive_omits_nested(tmp_path, fake_ingest):
    folder = tmp_path / "flat"
    _write_pdfs(folder, ["top.pdf"])
    _write_pdfs(folder / "deep", ["bottom.pdf"])

    payload = invoke_json(
        ["papers", "add-dir", str(folder), "--no-recursive"], tmp_path
    )
    assert payload["queued"] == 1


def test_add_dir_rejects_async_with_run(tmp_path, fake_ingest):
    folder = tmp_path / "x"
    _write_pdfs(folder, ["a.pdf"])
    result = runner.invoke(
        app, ["--json", "papers", "add-dir", str(folder), "--async", "--run"], env=_env(tmp_path)
    )
    assert result.exit_code != 0


def test_ingest_requires_table_id_or_all(tmp_path, fake_ingest):
    result = runner.invoke(app, ["--json", "papers", "ingest"], env=_env(tmp_path))
    assert result.exit_code != 0


def test_ingest_all_processes_every_queued_paper(tmp_path, fake_ingest):
    folder = tmp_path / "global"
    _write_pdfs(folder, ["a.pdf", "b.pdf"])
    invoke_json(["papers", "add-dir", str(folder), "--async"], tmp_path)

    payload = invoke_json(["papers", "ingest", "--all"], tmp_path)
    assert payload["count"] == 2
    assert [item["index_status"] for item in payload["items"]] == ["indexed"] * 2
