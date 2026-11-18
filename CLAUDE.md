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
  finds GitHub PR links, asks GitHub for reviewDecision, posts "<@people> waiting for review"
  as a reply in the thread of each PR still waiting (no LLM rewrites the text), and reacts
  to the Slack message of each finished PR (:white_check_mark: approved or merged, :x: closed unmerged; skipped if it already has either)
admin asks for a recurring check → schedule_pr_reminder checks once, then stores a schedules row (kind = pr_reminder)
scheduler → enqueue process_pr_reminder → worker (agent/pr_reminders/service.py::run_reminder, no LLM)
  → reminds in PR threads; soft-deletes the row (status != active) only once its optional time limit passes
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

**Dataset sync** (reference data Databricks → Supabase, e.g. the data access form's locations and tags):
```
scheduler (schedules row kind = dataset_sync) → enqueue process_dataset_sync → worker
  (dataset_sync/core/service.py::DatasetSyncService.run_scheduled_sync) → fetch_rows from Databricks
  → fully replace synced_datasets.synced_rows (a failure or empty result keeps the old copy; failures are posted to the schedule's channel)
admin asks the agent to sync / schedule → list_synced_datasets / sync_dataset / schedule_dataset_sync tools
request_data_access → reads the synced datasets, snapshots the form's dropdown options into Redis (access_form_options:{token})
  → receiver block_actions reads that snapshot (no Supabase) to open the modal
```

**Characters** (voices bound to features and skills, see `.claude/rules/architecture.md` "Characters"):
```
worker start (entrypoint.sh) → python -m worker.seed_characters inserts the seed files (src/characters/files/, in the image)
  whose name is missing from the characters table (insert-only; a failure never stops the worker). After that Supabase is
  the source of truth: admins ask the agent to list / add / update (fields, bindings = scope) / remove characters (the agent collects every required value, validates with dry_run, then asks for a yes before storing), or edit
  rows in Supabase. Every read re-validates a row and skips a bad one.
PR reminder (check_and_remind, run_reminder) → CharacterService.character_for_feature per thread → template voice
  (PrReminderVoice) + sender name/icon → post. Agent reply (run_agent) → character_for_skills after the router → persona
  appended to the system prompt → worker posts under the character (deletes the thinking placeholder).
One character per thread: thread_character:{channel}:{thread_ts} in Redis (14 days); a character bound to skill:core is the default voice of every task (none of the seed characters is).
```

**Agent loop** (`src/agent/agent.py`):
1. Router model (gpt-6.1-sol) picks skills from `src/agent/skills/files/*.md`
2. System prompt = always-loaded skills + routed skills + today's date, then the persona of the thread's character (picked from the characters bound to the routed skills, else to `skill:core`)
3. OpenAI Responses API tool calls (reasoning on, `AgentConfigs.REASONING_EFFORT`) → execute_query / get_job_run_details / schedule CRUD / check_pr_approvals / schedule_pr_reminder / request_data_access / generate_service_principal_secret / list_synced_datasets / sync_dataset / schedule_dataset_sync / list_characters / add_character / update_character / remove_character (tool schemas defined in `AgentConfigs.TOOLS`, `src/common/configs.py`)
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
make skills-sync      # replace the Supabase agent_skills table with src/agent/skills/files/ (reads SUPABASE_DB_URL)

docker compose logs -f receiver   # tail service logs
docker compose logs -f worker
docker compose logs -f scheduler

WORKER_COUNT=5 docker compose up -d  # scale workers
```

`make help` lists every target, including image publishing (`make docker-build-push`).

## Deployment

Remote deployment is not in this repo. Pushing a tag matching `agentic-slack-bot-vYYYY.MM.DD.<sha>` runs `.github/workflows/image-deploy.yaml`, which builds and pushes the image to Docker Hub. The image is then rolled out by the `slack_bot_deploy` role in the separate `ansible-infra` repo (`make check` / `make deploy ENV=staging IMAGE_TAG=<tag>`; also restart and stop playbooks). That repo owns the production compose file (a copy of `docker-compose.yml` with the pre-built image) and the `.env` handling — keep it in sync when `docker-compose.yml` or required env vars change. Agent skills do not need an image: the loader reads them from the Supabase `agent_skills` table, which the next section keeps in step with the files.

Image tags are created with the *Create Tag* workflow (`.github/workflows/create-tag.yaml`, run from `master`; pick the `type`, currently only `agentic-slack-bot`). A tag is `<type>-vYYYY.MM.DD.<sha>`, so a second tag of the same type for the same commit on the same day can't be created — to retry a failed run, use "Re-run jobs" on it instead.

DB migrations run automatically from `.github/workflows/db-migrate.yaml` (Flyway via `make db-migrate`) on every push to `master`. A `dorny/paths-filter` job checks whether the push touched `src/migrations/**`; if so, `migrate-dev` migrates the `development` database and then `migrate-staging` migrates `staging` (it needs `migrate-dev` to succeed). Each job reads the `SUPABASE_DB_URL` secret of the GitHub Environment of the same name. Flyway applies only the pending versions, so re-running is safe.

The same workflow syncs agent skills: a second path filter watches `src/agent/skills/files/**`, and when either filter matches, the same two jobs run `make skills-sync` (via `.github/actions/skills-sync`) *after* Flyway, replacing that environment's `agent_skills` table with the files. Running services pick the change up on their next request — no image tag or redeploy. The push that adds V13 runs the sync too, so the table is filled before the image that reads it is rolled out; if a sync fails, re-run the job or run `make skills-sync` by hand (until the table is filled every request fails).

## Environment Variables

See `.env.example`. Required:
- `SUPABASE_DB_URL` — PostgreSQL connection (bots, schedules, access_request_categories, access_requests, service_principal_secrets, synced_datasets tables)
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
- The token and signing secret are stored in Supabase Vault; `bots/infra/supabase_bots.py` resolves them by joining `vault.decrypted_secrets`. Admins rotate them with `vault.update_secret()` — no redeploy. The plaintext `bot_token` / `signing_secret` columns from V1 were dropped in V8.

**Supabase `schedules` table** — cron jobs:
- `id uuid`, `bot_id`, `cron`, `channel`, `question`
- `timezone` (IANA name the `cron` is read in; default `UTC`, a PR reminder is created in `Asia/Ho_Chi_Minh` unless the request names another), `kind` (`question` runs the agent; `pr_reminder` runs the PR reminder job; `dataset_sync` runs a dataset sync, `config.dataset` names it and `channel` is where failures are reported — `question` then holds a generated label), `status` (`active` / `completed` / `expired`; the scheduler only loads `active`, a finished reminder keeps its row), `config jsonb` (a reminder's scope, mentions, lookback, origin thread and saved progress — see `common/configs.py`)

**Supabase `access_request_categories` table** — gates the `request_data_access` and `generate_service_principal_secret` tools per bot/channel/request_type:
- `id uuid`, `bot_id`, `request_type`, `channel_ids text[]`, `reviewers text[]` (Slack user IDs allowed to approve/reject, or — for `service_principal_secret` — allowed to call the tool at all)

**Supabase `access_requests` table** — submitted requests + review state:
- `id uuid`, `bot_id`, `request_type`, `channel`, `thread_ts`, `requester_id`, `reviewers text[]`, `status` (`pending`/`approved`/`rejected`), `ticket_id`, `user_email`, `principal_type`, `display_name`, `principal`, `filter_column`, `allowed_value`, `scope_column`, `scope_value`, `groups text[]`, `tags text[]`, `service_principal_id`, `pr_url`, `expires_at` (24h from creation)

**Supabase `service_principal_secrets` table** — cached Databricks service-principal OAuth secrets, shared across all bots in the workspace (one row per `email`, replaced on rotation — including when the service principal itself is deleted and recreated with a new `client_id`):
- `id uuid`, `service_account` (svc-prefixed display name), `client_id`, `email` (bare address, unique, the lookup key), `dbx_secret_id`, `secret_hash`, `secret_encrypted bytea` (Fernet-encrypted), `status`, `dbx_create_time`, `dbx_update_time`, `dbx_expire_time`, `requested_by`

**Supabase `agent_skills` table** — the skill files copied into the database by `make skills-sync` (`name` PK, `filename`, `always`, `description`, `body`); workspace-wide, replaced whole on each sync.

**Supabase `synced_datasets` table** — reference data copied from Databricks, shared across all bots (see `.claude/rules/architecture.md` "Synced Datasets"):
- `dataset text` (PK), `description`, `source_sql` (read-only Databricks query), `enabled`, `column_names text[]`, `synced_rows jsonb` (last good snapshot, replaced whole), `row_count`, `last_synced_at`, `last_status` (`ok` / `error`), `last_error`
- Seeded by V11 with `store_locations` and `row_filter_tags`; both must be synced once after the first deploy or the data access form refuses to open.

**Supabase `characters` table** — voices the bot speaks in, shared across all bots (see `.claude/rules/architecture.md` "Characters"):
- `name text` (PK, slug), `display_name` (Slack sender name), `song`, `artist`, `enabled`, `bindings text[]` (`feature:<name>` / `skill:<name>`), `icon_emoji`, `icon_url` (nullable), `persona` (appended to the system prompt of an agent reply), `voices jsonb` (one block per feature, e.g. `{"pr_reminder": {"headers": [...]}}`)
- Filled from the seed files when a worker starts (insert-only, `worker/seed_characters.py`); after that added, changed and removed through the agent's character tools or in Supabase. Every read re-validates a row, so an invalid one is skipped, not fatal; check constraints keep `voices` a JSON object and `bindings` a flat array without nulls.

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
- Git holds the files; the loader (`agent/skills/loader.py`) reads only the Supabase `agent_skills` table, on every request — there is no fallback to the files in the image, and an empty table fails the request. Run `make skills-sync` to publish an edit (CI does it on merge to `master`).

See `.claude/rules/skills.md` for authoring guidelines.

## File Map

This is the **current** (legacy) layout, grouped by task. Do not add new modules to `agent/` or `connectors/db/`: new features follow [layered-architecture.md](.claude/rules/layered-architecture.md) (`src/<feature>/{presentation,core,infra}/`), and existing features move to it one at a time.

```
src/
  receiver/
    app.py                   # Webhook + interactivity handler (block_actions, view_submission)
    wiring.py                # Composition root: the bot repository
  worker/
    tasks.py                 # RQ task definitions (mentions, schedules, access request submission) — thin shell over agent/
    wiring.py                # Composition root: builds the migrated features' adapters + use cases, and the `ToolServices` the agent loop's tools run on
  # Migrated to the layered layout (layered-architecture.md): <feature>/{core,infra,presentation}/
  bots/
    core/                    # BotConfig model, BotRepository port, BotNotFoundError
    infra/supabase_bots.py   # Bot registry in Supabase; token + signing secret from Vault
  schedules/
    core/                    # Schedule / AddedReminder / AddedDatasetSync models, ScheduleRepository port
    infra/supabase_schedules.py   # Schedule CRUD, atomic PR-reminder / dataset-sync inserts (shared by the scheduler and the agent's tools)
    presentation/tools.py    # list/add/update/remove_schedule tool handlers
  dataset_sync/
    core/                    # models, errors, ports (DatasetRepository, DatasetFetcher, FailureNotifier), DatasetSyncService (sync with empty/shrink guards, scheduled run, scheduling, listing)
    infra/                   # supabase_datasets.py (registry + snapshots; get_rows is what other features read), databricks_fetcher.py, slack_notifier.py
    presentation/tools.py    # list_synced_datasets / sync_dataset / schedule_dataset_sync handlers
  characters/
    core/                    # Character / CharacterChanges (`apply_to` holds the update's merge rules) / CharacterOverview / RemovedCharacter / SeedResult models, ports (CharacterRepository, CharacterSource, KnownSkills, ThreadCharacterStore), validation (pure; run on every read), persona (the fixed `## Voice` block), CharacterService (pick for a feature or skills, list, add, update, remove, seed)
    infra/                   # supabase_characters.py, file_format.py + files_source.py (the seed files), known_skills.py, redis_thread_characters.py
    presentation/tools.py    # list_characters / add_character / update_character / remove_character handlers
    files/                   # seed files (character per file), inserted into the table by worker.seed_characters when a worker starts
  agent/                     # Everything the agent does, by task
    agent.py                 # Agent loop (OpenAI Responses API) + summarize_answer; returns an AgentReply (text + the character it spoke as)
    dispatch.py              # Tool schema exposure + per-call routing
    history.py               # Per-thread conversation history (Redis)
    skills/                  # loader.py (Supabase) / router.py / source.py (parsing) / sync.py (`make skills-sync`); files/ holds the agent (runtime) skill files (01–14) — NOT Claude Code skills
    forms/                   # Pure, no I/O — the receiver may import this
      access_request_options.py    # FormOptions built from the synced datasets (pure); Redis key for the snapshot
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
      message.py                   # Picks and fills in the reminder templates; the only live mentions are the validated user IDs it is given (pure)
      limits.py                    # Cron minimum interval, optional expiry check, cron-window and timezone checks, mention parsing, ReminderRequestError (pure)
      service.py                   # Collect links, check GitHub, post reminders, run one reminder tick
      tools.py                     # check_pr_approvals / schedule_pr_reminder handlers
  scheduler/
    app.py                   # Cron polling loop
    wiring.py                # Composition root: the schedule repository
  common/
    configs.py               # Env-backed config classes (Configs, GithubConfigs, AgentConfigs incl. tool schemas) + ReminderConfig (the `schedules.config` of a PR reminder) + ReminderMessages (the parts of a PR reminder's wording no character changes: age units, waiting line, quip thresholds, header rotation; the default wording is `DEFAULT_VOICE` in `agent/pr_reminders/message.py`)
    cron.py                  # Cron validation (malformed / too frequent) shared by recurring jobs
    crypto.py                # Fernet encrypt/decrypt for secrets cached at rest (service_principal_secrets)
  connectors/                # Generic clients for external tools — no task-specific logic
    db/                      # Supabase (Postgres) access — sync, psycopg
      connection.py              # psycopg connection helper
      agent_skills.py            # AgentSkill model + agent_skills table read / full replace (loader + `make skills-sync`)
    databricks/              # Statement API, Jobs API, SCIM, OAuth secrets
      client.py                  # Host/token/auth headers
      sql.py                     # SELECT-only Statement API client (run_query: capped, Slack-formatted; fetch_rows: every row, for dataset sync)
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
- [layered-architecture.md](.claude/rules/layered-architecture.md) — **where new code goes**: feature-first packages with `presentation/` / `core/` / `infra/` layers, ports, composition root, dependency rule. Required for all new code; the existing tree is migrated one feature at a time
- [code-style.md](.claude/rules/code-style.md) — Python style, linting, dependencies
- [skills.md](.claude/rules/skills.md) — agent skill file authoring (`src/agent/skills/files/`)
- [database.md](.claude/rules/database.md) — Supabase access: row dataclasses, where DB modules live, queries, migrations, tests

## Development Skills

Two unrelated things are called "skills" here:
- **Agent skills** — `src/agent/skills/files/*.md`, loaded into the Slack bot's system prompt at runtime.
- **Claude Code skills** — `.claude/skills/`, used by you while developing. Invoke `python-code-style` / `python-unit-tests` / `python-docstrings` when writing Python, invoke `layered-architecture` (with [layered-architecture.md](.claude/rules/layered-architecture.md), which takes precedence) before deciding where new code or a new module goes, and run `/code-review` on your changes before opening a PR. Details and precedence over the skills' defaults: [code-style.md](.claude/rules/code-style.md).

## Custom Commands

- `/add-skill` — scaffold a new skill file
- `/new-bot` — SQL template + checklist for registering a new bot
