# catena

`catena` is a local-first Python package for building Elicit-style evidence tables over PDFs.

The interface is a CLI. The app model is:

- papers are stored once in a global library;
- extraction tables select any subset of global papers;
- each user-created column is an atomic extraction question in one table;
- each cell is an evidence-backed BAML/LLM answer for one table, one paper, and one column.

## Stack

- `uv` for Python builds/environments.
- `mise` owns the project `uv` version.
- SQLite/SQLModel for source-of-truth data.
- Alembic for formal schema migrations.
- Docling for local PDF parsing/chunking.
- LanceDB for local vector retrieval.
- OpenAI-compatible gateway for all LLM and embedding calls.
- BAML for structured extraction outputs.

No local-model backend is configured or supported by default.

## Setup

Fill in `mise.local.toml`:

```toml
[env]
LLM_GATEWAY_BASE_URL = "https://your-gateway.example/v1"
LLM_GATEWAY_API_KEY = "..."
LLM_MODEL = "..."
LLM_EMBEDDING_MODEL = "..."
```

Then:

```bash
mise trust -a -y
mise run sync
mise exec -- uv run baml-cli generate
mise run api:init
```

`catena init` / `mise run api:init` creates local storage and runs Alembic migrations to `head`.
You can also manage migrations explicitly:

```bash
mise exec -- uv run catena db upgrade head
mise exec -- uv run catena db current
mise exec -- uv run catena db history

# Equivalent direct Alembic usage:
mise exec -- uv run alembic -c alembic.ini upgrade head
mise exec -- uv run alembic -c alembic.ini revision --autogenerate -m "describe change"
```

## Common CLI flow

```bash
# initialize .catena/ storage and the Default table
mise exec -- uv run catena init

# add and index a PDF globally, then attach it to the Default table
mise exec -- uv run catena papers add ./paper.pdf --title "My paper"

# create another table
mise exec -- uv run catena tables create "Screening table"

# attach an already indexed global paper to another table without reparsing/re-embedding
mise exec -- uv run catena tables add-paper 2 1

# add an extraction column to the Default table and run its queued cells
mise exec -- uv run catena columns add "Sample size" "What is the total sample size?" --run

# add an extraction column to another table
mise exec -- uv run catena columns add "Core method" "What is the core method?" --table-id 2 --run

# run all queued cells across all tables
mise exec -- uv run catena run

# run queued cells in one table
mise exec -- uv run catena run --table-id 2

# one-off Q&A over selected papers; no chat history is stored or reused
mise exec -- uv run catena ask "What is the core contribution?" --paper-id 1
mise exec -- uv run catena ask "Compare these papers." --paper-id 1 --paper-id 2
mise exec -- uv run catena ask "What themes appear in this table?" --table-id 2

# compute local embedding-based paper-pair similarity scores
mise exec -- uv run catena similarity compute
mise exec -- uv run catena similarity compute --table-id 2
mise exec -- uv run catena papers similar 1

# show one extraction matrix; defaults to the Default table
mise exec -- uv run catena table
mise exec -- uv run catena table --table-id 2
```

## Multi-table design

SQLite is authoritative. LanceDB is a rebuildable retrieval index derived from global paper chunks.

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

Adding a paper to another table creates table-specific queued cells only. It does not rerun Docling or embeddings.

Paper similarity scores are stored in SQLite and are derived from existing LanceDB chunk embeddings. The default algorithm averages normalized chunk vectors into one paper centroid, then stores cosine similarity for each paper pair.
