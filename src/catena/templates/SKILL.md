---
name: catena
description: >-
  Drive the catena CLI to build local, evidence-backed extraction tables over PDFs.
  Use when the user wants a literature-screening / Elicit-style matrix: import PDFs,
  define one atomic extraction question per column, and get cited answers per cell.
  Also use for one-off Q&A over a set of papers, or for importing a folder of PDFs
  into a table (including async ingestion). The source of truth is a local SQLite db
  plus a LanceDB vector index; all LLM/embedding calls go through an OpenAI-compatible
  gateway. Prefer the `--json` flag for machine-readable output.
---

# catena

`catena` is a local-first CLI for building evidence tables over PDFs. Data model:

- **papers** are stored once in a global library (parsed by Docling, chunked + embedded once);
- **extraction tables** select a subset of global papers;
- each **column** is one atomic extraction question (one table);
- each **cell** is an evidence-backed BAML/LLM answer (one table × one paper × one column).

SQLite is authoritative; LanceDB is a rebuildable retrieval index over global paper chunks.

## Before using it

Confirm the CLI is available:

```bash
catena --version 2>/dev/null || mise exec -- uv run catena --version
```

Run inside `~/.config/catena/config.toml` (or env vars / `mise.local.toml`). Check the
resolved config and gateway readiness:

```bash
catena config
```

If `gateway_ready` is `no`, LLM/embedding calls will fail — set `gateway_base_url`,
`gateway_api_key`, `llm_model`, `embedding_model` first.

## JSON contract (parse this, not the rich text)

Every command accepts a global `--json` flag emitting a stable envelope:

- `{"ok": true, ...}` for mutations (plus command-specific fields)
- `{"item": {...}}` for a single item
- `{"items": [...], "count": N}` for lists (may include `"message"` when empty)

```bash
catena --json papers add-dir ./cohort --async
# -> {"ok": true, "table_id": 7, "queued": 42, "next": "catena papers ingest --table-id 7"}
```

## Core flow

```bash
catena init                                # creates .catena/ storage + runs migrations + Default table
catena papers add ./paper.pdf --title "..." # parse + index a PDF, attach to Default table
catena papers add-dir ./cohort              # folder import into one table (idempotent)
catena tables create "Screening"
catena columns add "Sample size" "What is the total sample size?" --run
catena run                                  # run all queued cells across all tables
catena run --table-id 2
catena tables show                          # the extraction matrix; defaults to Default
```

Need the version under `mise` in a project? Prefix every command with
`mise exec -- uv run `.

## Folder imports & async ingestion (use this for batches)

`papers add-dir` imports a folder into one table **bound to the folder's resolved absolute
path** — same folder always maps to the same table (papers dedup by content hash; table and
memberships reused, never duplicated). Default is blocking (register → parse → index). For
large folders, use `--async` to register only, then ingest detached and poll:

```bash
catena --json papers add-dir ./cohort --async
# -> next: "catena papers ingest --table-id 7"
catena papers ingest --table-id 7            # parse + index the queued papers
catena papers import-status --table-id 7     # poll until indexed
catena papers ingest --table-id 7 --run      # also run the table's queued cells after parsing
catena papers ingest --table-id 7 --retry-failed
catena papers ingest --all                   # global scope instead of one table
```

`--async --run` is rejected (nothing parsed yet) — run `catena run --table-id N` after
`ingest` completes. Run long ingestion detached (e.g. via `zmx`) and poll `import-status`.

## One-off Q&A (no chat history stored)

```bash
catena ask "What is the core contribution?" --paper-id 1
catena ask "Compare these papers." --paper-id 1 --paper-id 2
catena ask "What themes appear in this table?" --table-id 2
```

## Attach an already-indexed paper to another table

Cheap — creates table-specific queued cells only, no rerun of Docling or embeddings:

```bash
catena tables add-paper 2 1     # table_id paper_id
```

## Operating rules

- Always pass `--json` when you need to parse results; the default rich output is for humans.
- After `papers add-dir --async`, follow the returned `next` hint: `papers ingest`, then poll
  `papers import-status` until counted before running extraction cells.
- Don't re-add or re-ingest papers that already exist — content-hash dedup makes re-imports
  idempotent, but blocking re-imports still redo nothing useful. Use `import-status` first.
- Keep secrets in env / `mise.local.toml [env]`; non-secret defaults in the config file.
- Local DB-only commands (`init`, `papers list`, `tables show`, `config`) run with no gateway
  configured; LLM calls (`run`, `columns add --run`, `ask`, `enrich`) require gateway settings.
