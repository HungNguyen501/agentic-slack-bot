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

**Data access request:**
```
user asks agent for access → request_data_access tool checks eligibility
  → posts "Open Form" button → receiver block_actions opens Slack modal
  → receiver view_submission validates fields, enqueues process_data_access_submission
  → worker inserts a pending access_requests row (24h expiry)
reviewer replies "approve <id>" / "reject <id>" in the thread
  → worker/review.py (bypasses the agent loop) resolves the principal via Databricks,
    opens a PR on VireoAI/vireox-data-platform via connectors/github.py, updates status
```

**Service principal secret generation** (synchronous, no separate approval step):
```
reviewer asks agent for a secret → generate_service_principal_secret tool validates
  the service_account is a well-formed email → checks channel + reviewer eligibility
  (access_request_categories) → verifies the service principal exists in Databricks
  → reuses a still-valid cached secret from Supabase
  (service_principal_secrets, encrypted) or mints a new one via Databricks
  → drops the plaintext into Redis (secret_link:{token}, short TTL) → posts a link
reviewer clicks the link → receiver GET /secrets/{token} → atomic Redis GETDEL
  → secret renders once, then is gone
```

**Agent loop** (`src/worker/agent.py`):
1. Router model (gpt-4o-mini) picks skills from `src/worker/skills/*.md`
2. System prompt = always-loaded skills + routed skills + today's date
3. OpenAI tool calls → execute_query / get_job_run_details / schedule CRUD / request_data_access / generate_service_principal_secret (tool schemas defined in `AgentConfigs.TOOLS`, `src/common/configs.py`)
4. Up to 10 iterations; history trimmed to last 20 pairs in Redis

## Quick Commands

```bash
make install          # uv sync --all-groups + pre-commit install
make lint             # ruff + flake8
make lint-sql         # sqlfluff against migrations + metric views
make build-image      # docker buildx build
make compose-up       # docker compose up -d --build
make compose-down     # docker compose down --remove-orphans
make compose-down-clean  # + remove volumes

make db-migrate       # apply pending Flyway migrations in src/migrations/ (reads SUPABASE_DB_URL)
make db-migrate-info  # show applied/pending migration status

docker compose logs -f receiver   # tail service logs
docker compose logs -f worker
docker compose logs -f scheduler

WORKER_COUNT=5 docker compose up -d  # scale workers
```

`make help` lists every target, including remote deployment (`make ansible-*`) and image publishing (`make docker-build-push`).

## Environment Variables

See `.env.example`. Required:
- `SUPABASE_DB_URL` — PostgreSQL connection (bots, schedules, access_request_categories, access_requests, service_principal_secrets tables)
- `OPENAI_API_KEY`
- `DATABRICKS_HOST` — workspace URL
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_ACCESS_TOKEN`
- `GIT_REPO_PAT_DATA_PLATFORM` — GitHub PAT for opening access-control PRs on `VireoAI/vireox-data-platform`
- `SECRET_ENCRYPTION_KEY` — Fernet key encrypting cached service-principal secrets at rest
- `SECRET_LINK_BASE_URL` — public base URL of the receiver, used to build one-time secret-view links
- `NGROK_AUTHTOKEN` — local dev only

Optional: `WORKER_COUNT` (default 2), `ROUTER_MODEL` (default gpt-4o-mini), `SCHEDULER_INTERVAL` (default 180 s)

## Data Models

**Supabase `bots` table** — bot registry (no per-bot env vars):
- `id`, `bot_token`, `signing_secret`, `enabled_skills text[]`, `admin_users text[]`, `app_id`, `active`

**Supabase `schedules` table** — cron jobs:
- `id uuid`, `bot_id`, `cron`, `channel`, `question`

**Supabase `access_request_categories` table** — gates the `request_data_access` and `generate_service_principal_secret` tools per bot/channel/request_type:
- `id uuid`, `bot_id`, `request_type`, `channel_ids text[]`, `reviewers text[]` (Slack user IDs allowed to approve/reject, or — for `service_principal_secret` — allowed to call the tool at all)

**Supabase `access_requests` table** — submitted requests + review state:
- `id uuid`, `bot_id`, `request_type`, `channel`, `thread_ts`, `requester_id`, `reviewers text[]`, `status` (`pending`/`approved`/`rejected`), `ticket_id`, `user_email`, `principal_type`, `display_name`, `principal`, `filter_column`, `allowed_value`, `scope_column`, `scope_value`, `groups text[]`, `tags text[]`, `service_principal_id`, `pr_url`, `expires_at` (24h from creation)

**Supabase `service_principal_secrets` table** — cached Databricks service-principal OAuth secrets, shared across all bots in the workspace (one row per `email`, replaced on rotation — including when the service principal itself is deleted and recreated with a new `client_id`):
- `id uuid`, `service_account` (svc-prefixed display name), `client_id`, `email` (bare address, unique, the lookup key), `dbx_secret_id`, `secret_hash`, `secret_encrypted bytea` (Fernet-encrypted), `status`, `dbx_create_time`, `dbx_update_time`, `dbx_expire_time`, `requested_by`

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
  receiver/app.py            # Webhook + interactivity handler (block_actions, view_submission)
  worker/
    agent.py                 # Agent loop + tool dispatch
    tasks.py                 # RQ task definitions (mentions, schedules, access request submission)
    review.py                # Deterministic approve/reject handling for access requests (no LLM)
    skills/                  # Markdown skill files (01–12)
  scheduler/app.py           # Cron polling loop
  common/
    configs.py               # Env-backed config classes (Configs, GithubConfigs, AgentConfigs incl. tool schemas)
    crypto.py                # Fernet encrypt/decrypt for secrets cached at rest (service_principal_secrets)
  models/                    # Pure data/rendering helpers, no I/O
    access_request_view.py       # Slack modal view + submission validation
    access_request_submission.py # VerifiedAccessRequest dataclass
    access_control_rules.py      # Renders rules_v2.yaml / service_principals.yaml entries
    access_request_pr.py         # Fills the data-platform repo's PR template
  connectors/
    db/                     # Supabase (Postgres) access — sync, psycopg
      bots.py                    # Bot registry
      schedules.py                # Schedule CRUD
      access_request_categories.py # Per-bot/channel request eligibility
      access_requests.py          # Submitted request CRUD
      service_principal_secrets.py # Cached (encrypted) service-principal secret CRUD
      connection.py                # psycopg connection helper
    databricks/              # Statement API, Jobs API, principal lookups
      sql.py                      # SELECT-only Statement API client
      jobs.py                     # Job run details
      principals.py                # User lookup
      service_principals.py        # Service principal find-or-create
      service_principal_secrets.py # OAuth client secret generation
    github.py                # GitHub REST client — opens access-control PRs on VireoAI/vireox-data-platform
    slack.py                 # Slack Web API — message posting + modal views
  databricks/metric_views/   # SQL views for semantic layer
  migrations/                # DB schema SQL (Flyway, applied via `make db-migrate`)
```

## Rules

Detailed guidelines in `.claude/rules/`:
- [architecture.md](.claude/rules/architecture.md) — service boundaries and invariants
- [code-style.md](.claude/rules/code-style.md) — Python style, linting, dependencies
- [skills.md](.claude/rules/skills.md) — skill file authoring

## Custom Commands

- `/add-skill` — scaffold a new skill file
- `/new-bot` — SQL template + checklist for registering a new bot
