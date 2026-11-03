# Code Style

## Language & Runtime

Python 3.12. Use `uv` for all dependency management (`uv add`, `uv remove`, `uv sync`). Never use pip directly.

## Formatting & Linting

Linter: `make lint` (runs ruff + flake8, check-only)
Line length: 170 characters (configured in `pyproject.toml`)

Never run `ruff format` — the codebase isn't formatted to its output and it rewrites dozens of unrelated files (blocked by a deny rule in `.claude/settings.local.json`). Match the surrounding style by hand.

Run `make lint` before considering any Python change complete.

## Imports

Standard library → third-party → local, separated by blank lines. No wildcard imports. Prefer absolute imports over relative ones.

Import specific names (`from connectors.db.bots import BotConfig`) where the surrounding code does; don't convert existing modules to import-the-module style.

## Type Hints

Use type hints on all function signatures. Use `dataclasses.dataclass` for plain data containers (see `BotConfig` in `src/connectors/bots.py`). Use `frozenset` for immutable sets loaded from the database.

## HTTP Clients

Use `httpx` for all outbound HTTP. Never add `requests` as a dependency.

## Async vs Sync

- `src/receiver/app.py` — async (FastAPI)
- `src/scheduler/app.py` — async (asyncio)
- `src/worker/`, `src/agent/` — sync (RQ workers run in threads; OpenAI client is sync)
- `src/connectors/` — sync

Do not mix async into worker/agent/connectors without a clear reason.

## Error Handling

Handle only errors that can actually occur at the boundary (Slack API, Databricks API, Supabase, Redis). Let unexpected exceptions propagate up to RQ's built-in retry/failure handling rather than swallowing them.

## Comments & Docstrings

No comments that describe what the code does. Add a comment only when the WHY is non-obvious: a hidden constraint, a Slack API quirk, a workaround for a specific API behavior.

Docstrings follow the style already in the file (mostly short Google-style summaries; see `src/agent/access_control/review.py`). Document public functions with non-obvious behavior; don't add boilerplate `Args:`/`Returns:` that only restate type hints, and never add docstrings to code you aren't otherwise changing.

## Development Skills

Project-local Claude Code skills in `.claude/skills/` apply to Python work here. **Repo config and the rules above win on any conflict** (e.g. the skills' Google-style 80-column limit and import-modules-only rule do not apply — 170 columns, name imports as above).

| Skill | Use for |
|---|---|
| `python-code-style` | Judgment calls on naming, errors, defaults, language features when writing or reviewing Python |
| `python-docstrings` | Adding or fixing docstrings, only when asked |
| `python-unit-tests` | Writing or reviewing pytest tests (see Testing below) |
| `code-review` | `/code-review` on your diff before opening a PR; CI also runs a remote review (`.github/workflows/code-review.yaml`) that fails on medium+ severity |

These are managed by the skills hub (`.claude/skills/.hub-skills.json`) — don't hand-edit them; changes are overwritten on the next sync. They are unrelated to the agent skills in `src/agent/skills/files/`.

## Testing

Tests live in `tests/`, mirroring `src/` (e.g. `src/connectors/slack.py` → `tests/connectors/test_slack.py`), and run with `make test` (also a CI step), which reports coverage with `--cov-fail-under=0` — coverage is reported, not yet enforced. Follow `python-unit-tests`:
- pytest is configured in `pyproject.toml` (`pythonpath = ["src"]`, `--import-mode=importlib`), so imports are rooted at `src/` and test files can share basenames. `tests/conftest.py` sets inert env defaults because the config classes read env vars at import time.
- Prefer the pure modules (`src/agent/forms/`, `src/agent/pr/`, `common/crypto.py`, `connectors/slack.split_message`); mock Slack/Databricks/GitHub/Supabase/Redis/OpenAI at the boundary (patch where the name is looked up, with `autospec=True`), never hit real services.
- Cover a bug fix with a test that fails on the old code.

## Dependencies

Before adding a new dependency, check if an existing one covers the need. New packages go in `pyproject.toml` via `uv add <package>`.
