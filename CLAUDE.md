# Agentic Slack Bot

Multi-bot, agent-driven Slack application that answers questions about Databricks data infrastructure using OpenAI's function-calling API and a skill-routing system.

## Architecture

Four containerized services sharing a Redis queue:

| Service | Entry point | Role |
|---|---|---|
| **receiver** | `src/receiver/app.py` | FastAPI webhook — verifies Slack HMAC-SHA256, deduplicates events in Redis, enqueues to `slack_events` RQ queue |
| **worker** | `src/worker/tasks.py` | RQ consumer — loads per-bot config from Supabase, runs the agent loop, posts reply chunks to Slack |
| **scheduler** | `src/scheduler/app.py` | asyncio polling loop — evaluates cron schedules every 180 s, enqueues `process_scheduled_question` jobs |
| **redis** | — | Message broker, event dedup store (600 s TTL), conversation history cache (24 h TTL) |

Single Docker image (`Dockerfile`) — `entrypoint.sh` selects the service via `SERVICE` env var.

## Key Flows

**User mention:**
```
Slack app_mention → receiver (HMAC verify, Redis dedup) → RQ queue
  → worker (load BotConfig from Supabase, run_agent, post reply chunks)
```

**Scheduled question:**
```
scheduler (croniter eval, Redis last-fired dedup)
  → enqueue process_scheduled_question → worker (post question + answer)
```

**Agent loop** (`src/worker/agent.py`):
1. Router model (gpt-4o-mini) picks skills from `src/worker/skills/*.md`
2. System prompt = always-loaded skills + routed skills + today's date
3. OpenAI tool calls → execute_query / get_job_run_details / schedule CRUD
4. Up to 10 iterations; history trimmed to last 20 pairs in Redis

## Quick Commands

```bash
make install          # uv sync --all-groups
make lint             # ruff + flake8
make build-image      # docker buildx build
make compose-up       # docker compose up -d --build
make compose-down     # docker compose down --remove-orphans
make compose-down-clean  # + remove volumes

docker compose logs -f receiver   # tail service logs
docker compose logs -f worker
docker compose logs -f scheduler

WORKER_COUNT=5 docker compose up -d  # scale workers
```

## Environment Variables

See `.env.example`. Required:
- `SUPABASE_DB_URL` — PostgreSQL connection (bots + schedules tables)
- `OPENAI_API_KEY`
- `DATABRICKS_HOST` — workspace URL
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_ACCESS_TOKEN`
- `NGROK_AUTHTOKEN` — local dev only

Optional: `WORKER_COUNT` (default 2), `ROUTER_MODEL` (default gpt-4o-mini), `SCHEDULER_INTERVAL` (default 180 s)

## Data Models

**Supabase `bots` table** — bot registry (no per-bot env vars):
- `id`, `bot_token`, `signing_secret`, `enabled_skills text[]`, `admin_users text[]`, `app_id`, `active`

**Supabase `schedules` table** — cron jobs:
- `id uuid`, `bot_id`, `cron`, `channel`, `question`

Bot is resolved at runtime: receiver uses `api_app_id` → `get_by_app_id()`; worker uses stored `bot_id` → `get_by_id()`.

## Skill System

Skills live in `src/worker/skills/NN_name.md` with YAML frontmatter:

```yaml
---
name: metadata
always: false
description: Use when the user asks about catalogs, schemas, tables, or column definitions.
---
```

- `always: true` → included in every request (core, filters, formatting)
- `always: false` → router model picks based on description + conversation context
- Per-bot `enabled_skills` array in Supabase gates which skills are available (empty = all)

See `.claude/rules/skills.md` for authoring guidelines.

## File Map

```
src/
  receiver/app.py          # Webhook handler (124 lines)
  worker/
    agent.py               # Agent loop + tool dispatch (444 lines)
    tasks.py               # RQ task definitions (185 lines)
    skills/                # Markdown skill files (01–10)
  connectors/
    bots.py                # Bot registry (Supabase)
    databricks.py          # Statement API + Jobs API client
    postgres.py            # Supabase schedule CRUD
  databricks/metric_views/ # SQL views for semantic layer
  migrations/              # DB schema SQL
```

## Rules

Detailed guidelines in `.claude/rules/`:
- [architecture.md](.claude/rules/architecture.md) — service boundaries and invariants
- [code-style.md](.claude/rules/code-style.md) — Python style, linting, dependencies
- [skills.md](.claude/rules/skills.md) — skill file authoring

## Custom Commands

- `/add-skill` — scaffold a new skill file
- `/new-bot` — SQL template + checklist for registering a new bot
