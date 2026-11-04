# Agentic Slack Bot

Multi-bot, agent-driven Slack application that answers questions about Databricks data infrastructure using OpenAI's Responses API (function calling) and a skill-routing system.

## Architecture

Four containerized services sharing a Redis queue:

| Service | Entry point | Role |
|---|---|---|
| **receiver** | `src/receiver/app.py` | FastAPI webhook — verifies Slack HMAC-SHA256, deduplicates events in Redis, enqueues to `slack_events` RQ queue |
| **worker** | `src/worker/tasks.py` | RQ consumer — loads per-bot config from Supabase, calls into `src/agent/` to run the agent loop, posts reply chunks to Slack |
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

**PR review reminder:**
```
user asks agent to check PR approval → check_pr_approvals tool reads the thread / channel history,
  finds GitHub PR links, asks GitHub for reviewDecision, posts "@channel waiting for review"
  as a reply in the thread of each PR still waiting (no LLM rewrites the text)
admin asks for a recurring check → schedule_pr_reminder checks once, then stores a schedules row (kind = pr_reminder)
scheduler → enqueue process_pr_reminder → worker (agent/pr_reminders/service.py::run_reminder, no LLM)
  → reminds in PR threads; soft-deletes the row (status != active) once nothing is waiting
```

**Data access request:**
```
user asks agent for access → request_data_access tool checks eligibility
  → posts "Open Form" button → receiver block_actions opens Slack modal
  → receiver view_submission validates fields, enqueues process_data_access_submission
  → worker inserts a pending access_requests row (24h expiry)
reviewer replies "approve <id>" / "reject <id>" in the thread
  → agent/access_control/review.py (bypasses the agent loop) resolves the principal via Databricks,
    opens a PR on 100xteam-ai/vireox-data-platform via agent/pr/data_platform_repo.py, updates status
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

**Agent loop** (`src/agent/agent.py`):
1. Router model (gpt-6.1-sol) picks skills from `src/agent/skills/files/*.md`
2. System prompt = always-loaded skills + routed skills + today's date
3. OpenAI Responses API tool calls (reasoning on, `AgentConfigs.REASONING_EFFORT`) → execute_query / get_job_run_details / schedule CRUD / check_pr_approvals / schedule_pr_reminder / request_data_access / generate_service_principal_secret (tool schemas defined in `AgentConfigs.TOOLS`, `src/common/configs.py`)
4. Up to 10 iterations; history trimmed to last 20 pairs in Redis

## Quick Commands

```bash
make install          # uv sync --all-groups + pre-commit install
make lint             # ruff + flake8
make lint-sql         # sqlfluff against migrations + metric views
make test             # pytest (tests/ mirrors src/)
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

`make help` lists every target, including image publishing (`make docker-build-push`).

## Deployment

Remote deployment is not in this repo. Pushing a tag matching `agentic-slack-bot-vYYYY.MM.DD.<sha>` runs `.github/workflows/image-deploy.yaml`, which builds and pushes the image to Docker Hub. The image is then rolled out by the `slack_bot_deploy` role in the separate `ansible-infra` repo (`make check` / `make deploy ENV=staging IMAGE_TAG=<tag>`; also restart and stop playbooks). That repo owns the production compose file (a copy of `docker-compose.yml` with the pre-built image) and the `.env` handling — keep it in sync when `docker-compose.yml` or required env vars change. Skills are baked into the image in deployed environments, so a skill edit reaches them only through a new image tag and redeploy.

Tags are created with the *Create Tag* workflow (`.github/workflows/create-tag.yaml`, run from `master`; pick `agentic-slack-bot`, `db-dev` or `db-staging`). A tag is `<type>-vYYYY.MM.DD.<sha>`, so a second tag of the same type for the same commit on the same day can't be created — to retry a failed run, use "Re-run jobs" on it instead.

DB migrations run from `.github/workflows/db-migrate.yaml` (Flyway via `make db-migrate`). Pushing a `db-dev-v…` tag migrates the `development` database and a `db-staging-v…` tag migrates `staging`; each tag must be on `master`, and the job reads the `SUPABASE_DB_URL` secret of the GitHub Environment of the same name.

## Environment Variables

See `.env.example`. Required:
- `SUPABASE_DB_URL` — PostgreSQL connection (bots, schedules, access_request_categories, access_requests, service_principal_secrets tables)
- `OPENAI_API_KEY`
- `DATABRICKS_HOST` — workspace URL
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_ACCESS_TOKEN`
- `GIT_REPO_PAT_DATA_PLATFORM` — GitHub PAT for opening access-control PRs on `100xteam-ai/vireox-data-platform` (or `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY` [+ `GITHUB_APP_INSTALLATION_ID`] for GitHub App auth, which takes precedence)
- `SECRET_ENCRYPTION_KEY` — Fernet key encrypting cached service-principal secrets at rest
- `SECRET_LINK_BASE_URL` — public base URL of the receiver, used to build one-time secret-view links
- `NGROK_AUTHTOKEN` — local dev only

Optional: `WORKER_COUNT` (default 2), `ROUTER_MODEL` (default gpt-6.1-sol), `SCHEDULER_INTERVAL` (default 180 s)

## Data Models

**Supabase `bots` table** — bot registry (no per-bot env vars):
- `id`, `bot_token_secret_id uuid`, `signing_secret_secret_id uuid`, `enabled_skills text[]`, `admin_users text[]`, `app_id`, `active`
- The token and signing secret are stored in Supabase Vault; `connectors/db/bots.py` resolves them by joining `vault.decrypted_secrets`. Admins rotate them with `vault.update_secret()` — no redeploy. The plaintext `bot_token` / `signing_secret` columns from V1 were dropped in V8.

**Supabase `schedules` table** — cron jobs:
- `id uuid`, `bot_id`, `cron`, `channel`, `question`
- `kind` (`question` runs the agent; `pr_reminder` runs the PR reminder job — `question` then holds a generated label), `status` (`active` / `completed` / `expired`; the scheduler only loads `active`, a finished reminder keeps its row), `config jsonb` (a reminder's scope, lookback and origin thread — see `common/configs.py`)

**Supabase `access_request_categories` table** — gates the `request_data_access` and `generate_service_principal_secret` tools per bot/channel/request_type:
- `id uuid`, `bot_id`, `request_type`, `channel_ids text[]`, `reviewers text[]` (Slack user IDs allowed to approve/reject, or — for `service_principal_secret` — allowed to call the tool at all)

**Supabase `access_requests` table** — submitted requests + review state:
- `id uuid`, `bot_id`, `request_type`, `channel`, `thread_ts`, `requester_id`, `reviewers text[]`, `status` (`pending`/`approved`/`rejected`), `ticket_id`, `user_email`, `principal_type`, `display_name`, `principal`, `filter_column`, `allowed_value`, `scope_column`, `scope_value`, `groups text[]`, `tags text[]`, `service_principal_id`, `pr_url`, `expires_at` (24h from creation)

**Supabase `service_principal_secrets` table** — cached Databricks service-principal OAuth secrets, shared across all bots in the workspace (one row per `email`, replaced on rotation — including when the service principal itself is deleted and recreated with a new `client_id`):
- `id uuid`, `service_account` (svc-prefixed display name), `client_id`, `email` (bare address, unique, the lookup key), `dbx_secret_id`, `secret_hash`, `secret_encrypted bytea` (Fernet-encrypted), `status`, `dbx_create_time`, `dbx_update_time`, `dbx_expire_time`, `requested_by`

Bot is resolved at runtime: receiver uses `api_app_id` → `get_by_app_id()`; worker uses stored `bot_id` → `get_by_id()`.

## Skill System

Skills live in `src/agent/skills/files/NN_name.md` with YAML frontmatter:

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
    tasks.py                 # RQ task definitions (mentions, schedules, access request submission) — thin shell over agent/
  agent/                     # Everything the agent does, by task
    agent.py                 # Agent loop (OpenAI Responses API) + summarize_answer
    dispatch.py              # Tool schema exposure + per-call routing
    history.py               # Per-thread conversation history (Redis)
    schedules.py             # Schedule-CRUD tool handlers
    skills/                  # loader.py / router.py; files/ holds the agent (runtime) skill files (01–13) — NOT Claude Code skills
    forms/                   # Pure, no I/O — the receiver may import this
      access_request_view.py       # Slack modal view + submission validation
      access_request_submission.py # VerifiedAccessRequest dataclass
    pr/
      access_control_rules.py      # Renders rules_v2.yaml / service_principals.yaml entries (pure)
      access_request_pr.py         # Fills the data-platform repo's PR template (pure)
      data_platform_repo.py        # Branch/commit/PR workflow on top of connectors/github.py
    access_control/
      access_request_categories.py # Per-bot/channel request eligibility (Supabase)
      access_requests.py           # Submitted request CRUD (Supabase)
      principals.py                # Databricks user / service-principal lookups
      service_principals.py        # Service principal find-or-create + group membership
      submission.py                # Persist a submitted form + post it for review
      review.py                    # Deterministic approve/reject handling (no LLM)
      tools.py                     # request_data_access tool handler
    service_principal_secrets/
      store.py                     # Cached (encrypted) service-principal secret CRUD (Supabase)
      tools.py                     # generate_service_principal_secret tool handler
    pr_reminders/
      pr_links.py                  # PR links in Slack messages + the thread each lives in (pure)
      queries.py                   # GitHub GraphQL query + response parser (pure)
      status.py                    # reviewDecision -> waiting / approved / ... bucket (pure)
      message.py                   # Reminder text; the only live mention is the @channel it adds (pure)
      limits.py                    # Cron minimum interval, reminder lifetime, 07:00-22:00 Vietnam-time window for scheduled ticks, ReminderRequestError (pure)
      service.py                   # Collect links, check GitHub, post reminders, run one reminder tick
      tools.py                     # check_pr_approvals / schedule_pr_reminder handlers
  scheduler/app.py           # Cron polling loop
  common/
    configs.py               # Env-backed config classes (Configs, GithubConfigs, AgentConfigs incl. tool schemas) + ReminderConfig (the `schedules.config` of a PR reminder)
    crypto.py                # Fernet encrypt/decrypt for secrets cached at rest (service_principal_secrets)
  connectors/                # Generic clients for external tools — no task-specific logic
    db/                      # Supabase (Postgres) access — sync, psycopg
      connection.py              # psycopg connection helper
      bots.py                    # Bot registry
      schedules.py               # Schedule CRUD (shared by the scheduler service and the agent's schedule tools)
    databricks/              # Statement API, Jobs API, SCIM, OAuth secrets
      client.py                  # Host/token/auth headers
      sql.py                     # SELECT-only Statement API client
      jobs.py                    # Job run details
      scim.py                    # SCIM list / create service principal / add group member
      service_principal_secrets.py # OAuth client secret generation
    github.py                # GitHub REST primitives (file, branch, PR) for 100xteam-ai/vireox-data-platform, plus a generic GraphQL call
    slack.py                 # Slack Web API — message posting, modal views, mrkdwn chunking
    openai_client.py         # Shared OpenAI client + Chat Completions / Responses wrappers
    redis_client.py          # Shared Redis client
  databricks/metric_views/   # SQL views for semantic layer
  migrations/                # DB schema SQL (Flyway, applied via `make db-migrate`)
.claude/
  rules/                     # Project rules (see below)
  commands/                  # /add-skill, /new-bot
  skills/                    # Claude Code dev skills (hub-managed): code-review, python-code-style, python-docstrings, python-unit-tests
  settings.example.json      # Template for the git-ignored .claude/settings.local.json
```

## Rules

Detailed guidelines in `.claude/rules/`:
- [architecture.md](.claude/rules/architecture.md) — service boundaries and invariants
- [code-style.md](.claude/rules/code-style.md) — Python style, linting, dependencies
- [skills.md](.claude/rules/skills.md) — agent skill file authoring (`src/agent/skills/files/`)

## Development Skills

Two unrelated things are called "skills" here:
- **Agent skills** — `src/agent/skills/files/*.md`, loaded into the Slack bot's system prompt at runtime.
- **Claude Code skills** — `.claude/skills/`, used by you while developing. Invoke `python-code-style` / `python-unit-tests` / `python-docstrings` when writing Python, and run `/code-review` on your changes before opening a PR. Details and precedence over the skills' defaults: [code-style.md](.claude/rules/code-style.md).

## Custom Commands

- `/add-skill` — scaffold a new skill file
- `/new-bot` — SQL template + checklist for registering a new bot
