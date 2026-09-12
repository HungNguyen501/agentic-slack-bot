# Architecture Rules

## Service Boundaries

**Receiver** (`src/receiver/`) does exactly two things: verify the Slack signature and enqueue the job. Never add agent logic, Databricks calls, or Supabase reads to this service.

Exceptions, both narrowly scoped to `/slack/interactivity`:
- Its `block_actions` handler calls `views.open` directly instead of enqueuing. Slack's `trigger_id` expires ~3 seconds after issuance — a window the RQ queue can't reliably guarantee under load — and the view is built by a pure function (`models.access_request_view.build_access_request_view`) from data already carried in the button's `value`, so no extra Supabase/Databricks I/O is introduced.
- Its `view_submission` handler runs `models.access_request_view.validate_submission()` — pure regex format validation, no I/O — synchronously before enqueuing, because Slack requires field errors on the view_submission response itself (`response_action: "errors"`) before the modal closes; an async worker reply would arrive after the modal is already gone. Anything requiring real I/O (the Databricks principal lookup) still happens in the worker task after enqueuing.

**Worker** (`src/worker/`) owns all agent logic. It must not listen on any port or call Slack Event API endpoints directly.

**Scheduler** (`src/scheduler/`) only evaluates cron schedules and enqueues jobs. It must not run the agent itself or post to Slack directly.

Services communicate exclusively through the Redis `slack_events` RQ queue. Never import across service boundaries (receiver ↔ worker ↔ scheduler).

## Bot Config

All per-bot configuration lives in the Supabase `bots` table. Never add per-bot env vars. New fields belong in the `bots` table with a migration in `src/migrations/`.

Bot resolution:
- Receiver → `get_by_app_id(api_app_id)` (uses Slack's `api_app_id` field)
- Worker/Scheduler → `get_by_id(bot_id)` (stored in the enqueued job payload)

## Redis Key Conventions

| Key pattern | TTL | Purpose |
|---|---|---|
| `seen:{event_id}` | 600 s | Receiver deduplication |
| `chat_history:{thread_ts}` | 24 h | Agent conversation history |
| `scheduler:last_fired:{sha256[:16]}` | 24 h | Scheduler dedup |

Do not add ad-hoc Redis keys outside these patterns without updating this table.

Note: the data access request button's TTL (`AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS`) is embedded as an `expires_at` timestamp in the Slack button's own `value` payload, not a Redis key — the receiver just compares it against the current time.

## Databricks Client

`src/connectors/databricks/` (`sql.py` specifically) must:
- Reject any SQL that is not a SELECT statement
- Enforce 200-row result cap (show truncation note when exceeded)
- Use wait_timeout=50 s, on_wait_timeout=CANCEL on the Statement API
- Never expose raw connection credentials — the PAT comes from the env, not from Supabase

## Agent Loop Invariants

- Max 10 tool-call iterations per request
- Conversation history trimmed to last 20 user/assistant pairs before saving
- Schedule-management tools (`category="schedule"`) are admin-only; check `bot.admin_users` before execution
- The `request_data_access` tool (`category="data_access"`) is channel-gated instead — it checks the Supabase `access_request_categories` table (`bot_id` + `request_type` + channel membership), not `admin_users`
- Authorization status (is_admin) is prepended to the question text, not stored in history

## Adding Tools to the Agent

New OpenAI tool schemas go in `AgentConfigs.TOOLS` (`src/common/configs.py`) as an `AgentTool(...)` entry with a `category` (`"core"`, `"schedule"`, or `"data_access"`). The corresponding dispatch branch goes in `_dispatch_tool()` in `src/worker/agent.py` (which routes by category to `_dispatch_schedule_tool()` / `_dispatch_data_access_tool()` / inline core handling). Keep tool names snake_case and match them exactly between the `AgentTool` entry and the dispatch branch.

## Skill Routing

Router calls must use `response_format={"type": "json_object"}` so the response is always parseable. If the router call fails for any reason, fall back to loading all enabled skills — never block the request.

## Data Access Requests

A parallel, mostly-deterministic flow alongside the main agent loop, for requesting and approving access to Databricks tables/row filters:

1. **Request** — the `request_data_access` tool (`_dispatch_data_access_tool` in `src/worker/agent.py`) checks the `access_request_categories` table for the bot/channel/request_type, then posts a Slack button. The button's `value` carries the request metadata (including `reviewers` and an `expires_at`) — nothing is written to Supabase yet.
2. **Form** — `block_actions`/`view_submission` on `/slack/interactivity` are the receiver's two documented exceptions above; the actual insert happens after enqueuing, in `worker.tasks.process_data_access_submission`, which writes a `pending` row to `access_requests` (`src/connectors/db/access_requests.py`) with a 24 h `expires_at`.
3. **Review** — a reviewer replies `approve <id>` / `reject <id>` in the thread. `reply_to_mention` (`src/worker/tasks.py`) detects this prefix and routes to `worker/review.py::handle_review_decision` **before** the LLM agent loop runs — approval/rejection is exact-keyword control flow, not an LLM tool call. It re-validates channel eligibility and reviewer membership (`user_id in row.reviewers`) at approval time, not just at submission time.
4. **Approval** — resolves the principal via Databricks (`connectors/databricks/principals.py` or `service_principals.py`), renders the `rules_v2.yaml`/`service_principals.yaml` snippets (`src/models/access_control_rules.py`), and opens (or reuses) a PR on `VireoAI/vireox-data-platform` via `src/connectors/github.py` — one branch/PR per `ticket_id`, check-then-create so retries and duplicate approvals never create duplicate branches, files, or PRs.

**GitHub client** (`src/connectors/github.py`) follows the same credential rule as the Databricks client: the PAT (`GIT_REPO_PAT_DATA_PLATFORM`) comes from the env, never from Supabase.
