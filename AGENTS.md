# Repository Guidelines

## Project Structure & Module Organization

`catena` is a Python package for local, evidence-backed PDF extraction tables. Source code lives in `src/catena/`, with the Typer CLI in `cli.py`, database models in `models.py`, migrations under `src/catena/migrations/`, and generated BAML client code in `src/catena/baml_client/`. BAML source files live in `baml_src/`. Tests are in `tests/`, and sample or reference PDFs are in `assets/`. Runtime data is created under `.catena/` and must stay untracked.

## Build, Test, and Development Commands

Run commands through `mise` so the pinned `uv` version is used:

```bash
mise trust -a -y
mise exec -- uv sync
mise exec -- uv run baml-cli generate
mise exec -- uv run catena init
mise exec -- uv run pytest
mise exec -- uv run ruff check .
```

Use `catena db upgrade head`, `catena db current`, and `catena db history` for Alembic migration work. Run `uv run catena --help` to inspect CLI commands.

## Coding Style & Naming Conventions

Target Python 3.11+. Use 4-space indentation, type annotations for public functions, and concise module-level helpers. Ruff is configured with a 100-character line length and lint rules `E`, `F`, `I`, `UP`, `B`, and `SIM`; generated BAML client files are excluded. Use `snake_case` for functions, variables, and modules, `PascalCase` for classes, and descriptive Typer command names.

## Testing Guidelines

Tests use `pytest` with `pytest-asyncio` in auto mode. Add tests in `tests/test_*.py`, mirroring the behavior or module under change. Prefer temporary paths and monkeypatched environment variables for filesystem and configuration tests. Run `mise exec -- uv run pytest` before submitting changes.

## Commit & Pull Request Guidelines

Use Linux kernel-style commit messages: a short imperative subject line, a blank line, and a wrapped body that explains the motivation and user-visible impact. Prefer subjects like `Add local web frontend` over vague summaries. Mention schema or BAML generation updates and list validation commands run. Add trailers such as `Signed-off-by:` only when explicitly requested.

## Security & Configuration Tips

Keep secrets in `mise.local.toml` or environment variables only. Do not commit `.catena/`, `.env`, gateway keys, generated runtime data, or local PDFs unless they are intentional test assets.
