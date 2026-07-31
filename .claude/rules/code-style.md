# Code Style

## Language & Runtime

Python 3.12. Use `uv` for all dependency management (`uv add`, `uv remove`, `uv sync`). Never use pip directly.

## Formatting & Linting

Formatter: `ruff format .`
Linter: `make lint` (runs ruff + flake8)
Line length: 170 characters (configured in `pyproject.toml`)

Run `make lint` before considering any Python change complete.

## Imports

Standard library → third-party → local, separated by blank lines. No wildcard imports. Prefer absolute imports over relative ones.

## Type Hints

Use type hints on all function signatures. Use `dataclasses.dataclass` for plain data containers (see `BotConfig` in `src/connectors/bots.py`). Use `frozenset` for immutable sets loaded from the database.

## HTTP Clients

Use `httpx` for all outbound HTTP. Never add `requests` as a dependency.

## Async vs Sync

- `src/receiver/app.py` — async (FastAPI)
- `src/scheduler/app.py` — async (asyncio)
- `src/worker/` — sync (RQ workers run in threads; OpenAI client is sync)
- `src/connectors/` — sync

Do not mix async into worker/connectors without a clear reason.

## Error Handling

Handle only errors that can actually occur at the boundary (Slack API, Databricks API, Supabase, Redis). Let unexpected exceptions propagate up to RQ's built-in retry/failure handling rather than swallowing them.

## Comments

No comments that describe what the code does. Add a comment only when the WHY is non-obvious: a hidden constraint, a Slack API quirk, a workaround for a specific API behavior.

## Dependencies

Before adding a new dependency, check if an existing one covers the need. New packages go in `pyproject.toml` via `uv add <package>`.
