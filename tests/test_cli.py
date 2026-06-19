import json
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from catena.cli import app
from catena.config import Settings
from catena.library import CatenaLibrary
from catena.models import Paper, PaperChunk, Status
from catena.parsing import ParsedChunk, ParsedDocument, ParsedPdfResult

runner = CliRunner()


def invoke_json(args: list[str], tmp_path):
    result = runner.invoke(app, ["--json", *args], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


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


def _env_with_home(tmp_path, monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CATENA_CONFIG", raising=False)
    env = _env(tmp_path)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    return env


def test_config_init_writes_default_location_and_reports_path(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    expected = tmp_path / "xdg" / "catena" / "config.toml"
    assert not expected.exists()

    result = runner.invoke(app, ["--json", "config", "init"], env=env)
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["path"] == str(expected)
    assert expected.is_file()
    written = expected.read_text(encoding="utf-8")
    assert "data_dir" in written
    assert "gateway_base_url" in written

    # the just-written file is now discoverable by `catena config`
    config_result = runner.invoke(app, ["--json", "config"], env=env)
    assert config_result.exit_code == 0, config_result.output
    config_payload = json.loads(config_result.output)
    assert config_payload["item"]["config_path"] == str(expected)


def test_config_init_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    target = tmp_path / "xdg" / "catena" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# pre-existing\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "config", "init"], env=env)
    assert result.exit_code == 1, result.output

    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "already exists" in payload["error"]
    assert payload["path"] == str(target)
    assert target.read_text(encoding="utf-8") == "# pre-existing\n"  # untouched


def test_config_init_force_overwrites(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    target = tmp_path / "xdg" / "catena" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# stale\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "config", "init", "--force"], env=env)
    assert result.exit_code == 0, result.output
    assert "data_dir" in target.read_text(encoding="utf-8")


def test_config_init_path_override(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    custom = tmp_path / "nested" / "custom.toml"

    result = runner.invoke(
        app, ["--json", "config", "init", "--path", str(custom)], env=env
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["path"] == str(custom)
    assert custom.is_file()
    assert custom.parent.exists()  # parent dirs created


def test_skill_install_writes_default_location(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    expected = tmp_path / "home" / ".agents" / "skills" / "catena" / "SKILL.md"
    assert not expected.exists()

    result = runner.invoke(app, ["--json", "skill", "install"], env=env)
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["name"] == "catena"
    assert payload["path"] == str(expected)
    assert expected.is_file()
    text = expected.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: catena" in text


def test_skill_install_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    root = tmp_path / "home" / ".agents" / "skills"
    target = root / "catena" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# pre-existing\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "skill", "install"], env=env)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "already exists" in payload["error"]
    assert target.read_text(encoding="utf-8") == "# pre-existing\n"  # untouched


def test_skill_install_force_overwrites(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    target = tmp_path / "home" / ".agents" / "skills" / "catena" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# stale\n", encoding="utf-8")

    result = runner.invoke(app, ["--json", "skill", "install", "--force"], env=env)
    assert result.exit_code == 0, result.output
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "# stale\n" not in text


def test_skill_install_dir_and_name_overrides(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    env["CATENA_SKILLS_DIR"] = str(tmp_path / "default-skills")  # env override
    custom_root = tmp_path / "custom" / "skills"

    result = runner.invoke(
        app,
        ["--json", "skill", "install", "--dir", str(custom_root), "--name", "lit-review"],
        env=env,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    custom_target = custom_root / "lit-review" / "SKILL.md"
    assert payload["path"] == str(custom_target)
    assert custom_target.is_file()
    # --dir wins over CATENA_SKILLS_DIR env
    assert not (tmp_path / "default-skills").exists()


def test_skill_install_uses_env_default_skills_dir(tmp_path, monkeypatch):
    env = _env_with_home(tmp_path, monkeypatch)
    env["CATENA_SKILLS_DIR"] = str(tmp_path / "env-skills")

    result = runner.invoke(app, ["--json", "skill", "install"], env=env)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["path"] == str(tmp_path / "env-skills" / "catena" / "SKILL.md")


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


def test_add_dir_json_keeps_stdout_parseable_and_writes_progress_to_stderr(
    tmp_path,
    fake_ingest,
):
    folder = tmp_path / "cohort"
    _write_pdfs(folder, ["a.pdf", "b.pdf"])

    result = runner.invoke(app, ["--json", "papers", "add-dir", str(folder)], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["queued"] == 2
    assert "[catena] step=parse" in result.stderr
    assert "Starting Docling batch parse" in result.stderr
    assert "step=index" in result.stderr
    assert "step=complete" in result.stderr


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


def test_ingest_json_keeps_stdout_parseable_and_writes_progress_to_stderr(
    tmp_path,
    fake_ingest,
):
    folder = tmp_path / "papers"
    _write_pdfs(folder, ["one.pdf"])
    registered = invoke_json(["papers", "add-dir", str(folder), "--async"], tmp_path)

    result = runner.invoke(
        app,
        ["--json", "papers", "ingest", "--table-id", str(registered["table_id"])],
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert "[catena] step=queued" in result.stderr
    assert "step=complete" in result.stderr


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


def test_search_json_returns_local_text_hits(tmp_path):
    library = CatenaLibrary(Settings(data_dir=tmp_path))
    library.init()
    with Session(library.engine, expire_on_commit=False) as session:
        paper = Paper(
            title="Fast Local Search",
            source_path="paper.pdf",
            parse_status=Status.PARSED,
            index_status=Status.INDEXED,
        )
        session.add(paper)
        session.commit()
        session.refresh(paper)
        assert paper.id is not None
        session.add(
            PaperChunk(
                paper_id=paper.id,
                chunk_index=0,
                text="SQLite FTS provides fast local exact text retrieval.",
                page_start=2,
                heading="Search",
            )
        )
        session.commit()
    library.rebuild_search_index()

    payload = invoke_json(["search", "exact text retrieval", "--mode", "text"], tmp_path)

    assert payload["query"] == "exact text retrieval"
    assert payload["mode"] == "text"
    assert payload["count"] == 1
    assert payload["items"][0]["paper_title"] == "Fast Local Search"
    assert payload["items"][0]["page_start"] == 2
