# catena

A local-first CLI for building Elicit-style evidence tables over PDFs. Papers are stored
once in a global library; extraction tables select subsets; each column is one atomic
extraction question; each cell is an evidence-backed BAML/LLM answer.

## Stack

`uv` · SQLite/SQLModel (source of truth) · Alembic (migrations) · Docling (PDF parsing)
· LanceDB (vector retrieval) · OpenAI-compatible gateway (LLM + embeddings) · BAML
(structured extraction).

## Installation

Requires Python 3.11+. Remote: <https://github.com/khangkontum/catena>.

```bash
mise install 'pipx:git+https://github.com/khangkontum/catena.git@main'   # mise
uv tool install git+https://github.com/khangkontum/catena.git            # uv
pip install git+https://github.com/khangkontum/catena.git                # pip
```

## Agent skill

Install a catena skill so coding agents (Pi, Claude Code, Codex, …) know how to drive
the CLI. Writes `SKILL.md` to the default skills root (`~/.agents/skills`, or
`$CATENA_SKILLS_DIR`):

```bash
catena skill install                # ~/.agents/skills/catena/SKILL.md
catena skill install --force         # overwrite
catena skill install --dir DIR       # custom skills root
catena skill install --name lit-review
```

## Setup

Settings resolve in this order (highest first): environment variables (`LLM_*`,
`CATENA_*`) → a discovered TOML config file → defaults. `catena config` shows which
file is loaded and the full search order.

### Config file

Auto-discovered from the first existing path:

1. `$CATENA_CONFIG`
2. `./catena.toml`
3. `$XDG_CONFIG_HOME/catena/config.toml` → `~/.config/catena/config.toml`
4. `~/.config/catena/catena.toml`
5. `~/.config/catena.toml`
6. `~/catena.toml`
7. `~/.catena.toml`

```toml
# ~/.config/catena/config.toml — configure once, use everywhere
data_dir = "~/catena-data"
gateway_base_url = "https://api.openai.com/v1"
gateway_api_key = "..."
llm_model = "gpt-4o"
embedding_model = "text-embedding-3-small"
top_k = 12
```

Keys: `data_dir`, `gateway_base_url`, `gateway_api_key`, `llm_model`,
`embedding_model`, `embedding_batch_size`, `top_k`, `llm_temperature`. Omitted fields
fall back to env vars then defaults — keep secrets in `mise.local.toml [env]`.

Write a starter config to the default location (`~/.config/catena/config.toml`):

```bash
catena config init                # errors if it already exists
catena config init --force        # overwrite
catena config init --path FILE    # write elsewhere
```

### Environment variables

```toml
# mise.local.toml (per-project, untracked)
[env]
LLM_GATEWAY_BASE_URL = "https://your-gateway.example/v1"
LLM_GATEWAY_API_KEY = "..."
LLM_MODEL = "..."
LLM_EMBEDDING_MODEL = "..."
```

One-time init creates `.catena/` storage and runs migrations to head:

```bash
catena init
```

Manage migrations explicitly: `catena db upgrade head` / `current` / `history`.

## Common CLI flow

```bash
catena papers add ./paper.pdf --title "My paper"
catena papers add-dir ./cohort          # folder import, idempotent
catena tables create "Screening table"
catena tables add-paper 2 1             # attach global paper to another table
catena columns add "Sample size" "What is the total sample size?" --run
catena columns add "Core method" "What is the core method?" --table-id 2 --run
catena run                              # all queued cells across all tables
catena run --table-id 2
catena ask "What is the core contribution?" --paper-id 1
catena ask "Compare these papers." --paper-id 1 --paper-id 2
catena tables show                      # the matrix; defaults to the Default table
```

## Folder imports & async ingestion

`papers add-dir` imports a folder into one table **bound to the folder's resolved absolute
path** — the same folder always maps to the same table. The path is stored on the table as `source_path`.

Blocking by default (register → parse → index). Use `--async` to register only and defer
parsing to `papers ingest` (run detached, poll `papers import-status`):

```bash
catena papers add-dir ./cohort --async
# -> {"ok": true, "table_id": 7, "queued": 42, "next": "catena papers ingest --table-id 7"}
catena papers ingest --table-id 7
catena papers import-status --table-id 7    # poll until indexed
catena papers ingest --table-id 7 --run     # also run queued cells after parsing
catena papers ingest --table-id 7 --retry-failed
```

Run `catena run --table-id N` after `ingest`. `ingest` is scoped to one table; `--all` ingests globally.

## JSON output

Every command takes a global `--json` flag emitting a stable envelope
(`ok`/`item`/`items`/`count` + command-specific fields).

```bash
catena --json papers add-dir ./cohort --async
```

## Multi-table design

SQLite is authoritative; LanceDB is a rebuildable retrieval index over global paper
chunks.

```txt
.catena/
  catena.sqlite        # papers, tables, memberships, columns, cells
  lancedb/             # global chunk vectors, keyed by paper_id/chunk_id
  papers/
    1/
      source.pdf
      document.md
      docling.json
```

Adding a paper to another table creates table-specific queued cells only — no rerun of
Docling or embeddings.
