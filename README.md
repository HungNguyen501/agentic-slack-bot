# Agentic Slack Bot

An agentic Slack bot that answers questions about your Databricks data infrastructure — catalogs, schemas, tables, columns, jobs, lineage, query history, usage costs, and data access control.

Powered by OpenAI with function calling. A lightweight router model selects only the relevant skill context for each question; the main model then queries Databricks system tables on demand and replies in-thread with full conversation context.

Multiple independent bots (one per Slack workspace or use-case) are supported. All bot credentials and configuration live in a `bots` table in Supabase — no per-bot env vars needed.

## Architecture

![workflow](./docs/workflow.jpg)

## Services

| Service | Responsibility |
|---|---|
| **receiver** | FastAPI app that accepts incoming Slack webhook events and interactive payloads. Resolves the bot by Slack `api_app_id` from Supabase, verifies the per-bot HMAC-SHA256 signature, deduplicates events via Redis, and enqueues `app_mention` payloads onto the `slack_events` RQ queue. Also handles the two Slack interactivity endpoints (`block_actions`, `view_submission`) for the data access request modal — see [below](#data-access-requests). |
| **worker** | RQ consumer that processes queued Slack events. Loads per-bot config (token, skills, admin users) from Supabase, runs the OpenAI agent loop, executes Databricks SQL queries, fetches job run error details, manages scheduled questions, handles data access request submissions and reviewer approve/reject decisions, and posts replies back to Slack threads. Scales horizontally via `WORKER_COUNT`. |
| **scheduler** | Background loop that polls Supabase every `SCHEDULER_INTERVAL` seconds. Evaluates each saved cron schedule against the current time and enqueues `process_scheduled_question` jobs for any that are due, passing the schedule's `bot_id` so the correct bot posts the answer. |
| **redis** | Message broker and deduplication store. Distributes jobs between workers (competing-consumer), tracks seen Slack event IDs, and records per-schedule last-fired timestamps. |
| **ngrok** | Tunnels `receiver:8123` to a public HTTPS URL so Slack can reach the bot during local development. |

## Modules

| Module | Responsibility |
|---|---|
| `src/receiver/` | Slack webhook handler — per-bot signature verification, URL challenge, event deduplication, RQ enqueue, and the two synchronous interactivity exceptions (modal open, form validation) documented in `.claude/rules/architecture.md`. |
| `src/worker/agent.py` | Agent core — OpenAI function-calling loop, skill loading and routing, tool dispatch (SQL, job details, schedule CRUD, data access requests), and thread history management. |
| `src/worker/review.py` | Deterministic (non-LLM) handling of reviewer `approve`/`reject` replies on data access requests. |
| `src/scheduler/` | Cron scheduler — reads schedules from Supabase, evaluates firing windows, and enqueues periodic questions per bot. |
| `src/common/configs.py` | Env-backed config classes shared across services — `Configs`, `GithubConfigs`, and `AgentConfigs` (including the agent's OpenAI tool schemas). |
| `src/models/` | Pure data/rendering helpers with no I/O — the Slack modal view + validation, the verified-request dataclass, and the `rules_v2.yaml`/PR template renderers for data access requests. |
| `src/connectors/db/` | Supabase (Postgres) clients — bot registry, schedule CRUD, and data-access-request category/submission CRUD. |
| `src/connectors/databricks/` | Databricks clients — Statement API (SQL queries), Jobs REST API (run details), and SCIM user/service-principal lookups. |
| `src/connectors/github.py` | GitHub REST client — opens the access-control PR on `VireoAI/vireox-data-platform` when a data access request is approved. |
| `src/connectors/slack.py` | Slack Web API client — message posting and modal views. |
| `src/worker/skills/` | Markdown skill files loaded into the agent system prompt. The router selects which skills to include based on the user's question. |
| `src/databricks/metric_views/` | SQL view definitions for the semantic layer (`vw_dbu_cost`, `vw_job_run_stats`, `vw_query_perf`) deployed to `vireox_infra.semantic` in Databricks. |

## What it can answer

- How many catalogs / schemas / tables exist, and their column definitions
- Databricks job configs, schedules, task dependencies, and run history (including per-task error details)
- Data lineage — upstream and downstream table dependencies
- Query execution history — who ran what, when, duration, status
- Platform DBU consumption and estimated cost by workspace, SKU, or user
- Data access control — which tables a user can see, which users can access a table
- Aggregated metrics via semantic views — cost trends, job success/failure rates, query performance stats
- Scheduled reports — list, create, update, and remove cron-based automated questions (admin-only)
- Data access requests — start a request for row-filter or scoped access from a Slack thread; a designated reviewer approves or rejects it in-thread, which provisions the Databricks principal and opens a governance PR automatically (see [Data access requests](#data-access-requests))

Questions about business data values (revenue, customer counts, etc.) are out of scope and politely declined.

Data window: **last 180 days** for all event and history tables.

## Setup

### 1. Create the database tables

Run `make db-migrate` to apply the [Flyway migrations](src/migrations/) and create the `bots`, `schedules`, `access_request_categories`, and `access_requests` tables.

### 2. Register your bot in Supabase

Insert one row per Slack app into the `bots` table:

```sql
INSERT INTO bots (id, bot_token, signing_secret, enabled_skills, admin_users, app_id, active)
VALUES (
  'my-bot',                          -- unique slug
  'xoxb-...',                        -- OAuth bot token from Slack app config
  'your_signing_secret',             -- signing secret from Slack app config
  '{}',                              -- empty = all skills; or e.g. '{"jobs","billing"}'
  ARRAY['U08UQ1FG39S'],              -- Slack user IDs allowed to manage schedules
  'A08XXXXXX',                       -- Slack App ID
  true
);
```

- **Bot token** and **signing secret**: Slack app config → Basic Information / OAuth & Permissions
- **App ID**: shown as `api_app_id` in any Slack event payload, or on the Slack app's Basic Information page

See [`/new-bot`](.claude/commands/new-bot.md) for the full checklist, including the optional `access_request_categories` row needed to let a bot handle data access requests.

### 3. Fill in `.env`

```bash
cp .env.example .env
# Fill in SUPABASE_DB_URL, OPENAI_API_KEY, DATABRICKS_*, GIT_REPO_PAT_DATA_PLATFORM, NGROK_AUTHTOKEN
```

### 4. Start services

```bash
docker compose up --build
```

### 5. Finish Slack setup

Open http://localhost:4040 → copy the `https://...ngrok-free.app` URL.

Back in Slack app config:
- **Event Subscriptions** → toggle **On**
- **Request URL**: `https://<your-ngrok>.ngrok-free.app/slack/events`
- Slack pings the URL; receiver responds to the challenge → ✅ Verified
- **Subscribe to bot events** → add `app_mention`
- **Save Changes** → reinstall the app if prompted.

### 6. Test

Invite the bot to a channel: `/invite @yourbot`

Try asking:
```
@yourbot how many tables are in the gold schema?
@yourbot show me failed job runs in the last 7 days
@yourbot which jobs cost the most last month?
@yourbot what tables can alice@example.com access?
@yourbot what is the p95 job duration for the ingestion pipeline?
```

Follow-up questions work — the bot remembers the thread conversation for 24 hours.

## Adding a second bot

1. Create a second Slack app at https://api.slack.com/apps
2. Insert another row into the `bots` table with its token, signing secret, and app ID
3. No deployment changes needed — the receiver resolves bots dynamically at runtime

Each bot can have its own `enabled_skills` (to restrict what it can answer) and `admin_users` (to control who can manage its schedules).

## Scheduled reports

Admins (users in `bot.admin_users`) can ask the bot to repeat a question on a cron cadence: "schedule a report every weekday at 9am UTC in #data-alerts asking about failed jobs" calls the `add_schedule`/`list_schedules`/`update_schedule`/`remove_schedule` agent tools like any other request. A separate `scheduler` service polls the `schedules` table every `SCHEDULER_INTERVAL` seconds and, when one is due, runs the same agent loop and posts the question + answer as a new Slack thread.

See [`docs/scheduled_reports.md`](docs/scheduled_reports.md) for the full design (firing/dedup logic, schema, known gaps).

## Scaling workers

Set `WORKER_COUNT` in `.env` and restart:

```bash
docker compose up -d
```

Each worker container handles one question at a time. Redis distributes jobs to the first free worker (competing-consumer), so load is spread evenly without any round-robin configuration.

## Updating agent instructions

Instructions live in [`src/worker/skills/`](src/worker/skills/) as individual Markdown files — no code changes or image rebuild needed for edits in most cases.

| File | Purpose | Always loaded |
|---|---|---|
| `01_core.md` | Identity, scope, query rules | Yes |
| `02_filters.md` | Mandatory catalog/time scope filters | Yes |
| `03_metadata.md` | `information_schema` tables | Routed |
| `04_jobs.md` | Lakeflow jobs and run timelines | Routed |
| `05_lineage.md` | `access.table_lineage` | Routed |
| `06_billing.md` | Raw billing and query history tables | Routed |
| `07_access.md` | Data access control tables | Routed |
| `08_formatting.md` | Slack output formatting rules | Yes |
| `09_schedules.md` | Scheduled report management (admin) | Routed |
| `10_semantic.md` | Semantic views for aggregated metrics | Routed |
| `11_data_access_requests.md` | Starting a data access request | Routed |
| `12_service_principal_secrets.md` | Generating a service-principal secret (reviewers only) | Routed |

**Always-loaded** skills are included in every system prompt. **Routed** skills are selected per-request by a fast router model (`gpt-4o-mini` by default) based on the user's question and recent conversation history.

### Adding a new skill

Create a numbered `.md` file in `src/worker/skills/` with this frontmatter:

```markdown
---
name: my-skill
always: false
description: Use when the question is about X, Y, or Z.
---

## Skill: My Skill

...content...
```

The router uses the `description` field to decide when to load it. Set `always: true` to load it on every request regardless.

To restrict a skill to specific bots, set `enabled_skills` on the bot's row in Supabase. An empty array means all skills are available.

## Data access requests

A user can ask the bot to request row-filter (or tag-scoped) access to a table directly from a Slack thread:

1. The agent's `request_data_access` tool checks the `access_request_categories` table for the bot/channel/request type, then posts an "Open Form" button.
2. Clicking the button opens a Slack modal (handled synchronously by the receiver — see `.claude/rules/architecture.md`); submitting it creates one `pending` row per requested email in `access_requests` (24h expiry).
3. A reviewer (from that category's `reviewers` list) replies `approve <id>` or `reject <id>` in the thread. This bypasses the LLM agent loop entirely — it's deterministic control flow in `src/worker/review.py`.
4. On approval, the bot resolves the Databricks principal (user or service principal), and opens a PR against `VireoAI/vireox-data-platform` appending the new rule to `rules_v2.yaml`.

New request categories are added by inserting a row into `access_request_categories` — no code changes needed. See [`docs/data_access_request_form.md`](docs/data_access_request_form.md) for the full design (sequence diagram, schema, and field reference).

## Service principal secrets

A configured reviewer can ask the bot to generate a Databricks service-principal OAuth secret — synchronously, with no separate approval step:

1. The agent's `generate_service_principal_secret` tool checks the `access_request_categories` table (`request_type="service_principal_secret"`) for **both** channel eligibility and that the requester is on that category's `reviewers` list.
2. It verifies the service principal exists in Databricks, then reuses a still-valid cached secret from `service_principal_secrets` (encrypted at rest) or mints a new one and caches it.
3. The full record is dropped into Redis under a fresh, single-use token, and a plain link is posted to Slack — never the secret itself.
4. `GET /secrets/{token}` on the receiver serves it exactly once via an atomic Redis `GETDEL`, then it's gone.

See [`docs/service_principal_secrets.md`](docs/service_principal_secrets.md) for the full design (sequence diagram, schema, and known gaps).

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_DB_URL` | Yes | PostgreSQL connection string for Supabase |
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `DATABRICKS_HOST` | Yes | Databricks workspace URL |
| `DATABRICKS_WAREHOUSE_ID` | Yes | SQL warehouse ID for statement execution |
| `DATABRICKS_ACCESS_TOKEN` | Yes | Databricks personal access token |
| `GIT_REPO_PAT_DATA_PLATFORM` | Yes | GitHub PAT (fine-grained, contents + pull_requests write) scoped to `VireoAI/vireox-data-platform`, used to open access-control PRs when a data access request is approved |
| `SECRET_ENCRYPTION_KEY` | Yes | Fernet key encrypting cached service-principal secrets at rest |
| `SECRET_LINK_BASE_URL` | Yes | Public base URL of the receiver, used to build one-time secret-view links |
| `NGROK_AUTHTOKEN` | Yes | ngrok auth token (local dev only) |
| `WORKER_COUNT` | No | Number of concurrent worker containers (default: `2`) |
| `ROUTER_MODEL` | No | Model used for skill routing (default: `gpt-4o-mini`) |
| `SCHEDULER_INTERVAL` | No | Seconds between scheduler polling ticks (default: `180`) |
