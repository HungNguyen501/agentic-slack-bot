# Architecture Rules

## Service Boundaries

**Receiver** (`src/receiver/`) does exactly two things: verify the Slack signature and enqueue the job. Never add agent logic, Databricks calls, or Supabase reads to this service.

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
| `event:{event_id}` | 600 s | Receiver deduplication |
| `chat_history:{thread_ts}` | 24 h | Agent conversation history |
| `scheduler:last_fired:{sha256[:16]}` | — | Scheduler dedup (no expiry) |

Do not add ad-hoc Redis keys outside these patterns without updating this table.

## Databricks Client

`src/connectors/databricks.py` must:
- Reject any SQL that is not a SELECT statement
- Enforce 200-row result cap (show truncation note when exceeded)
- Use wait_timeout=50 s, on_wait_timeout=CANCEL on the Statement API
- Never expose raw connection credentials — the PAT comes from the env, not from Supabase

## Agent Loop Invariants

- Max 10 tool-call iterations per request
- Conversation history trimmed to last 20 user/assistant pairs before saving
- Schedule-management tools are admin-only; check `bot.admin_users` before execution
- Authorization status (is_admin) is prepended to the question text, not stored in history

## Adding Tools to the Agent

New OpenAI tool schemas go in the `TOOLS` list in `src/worker/agent.py`. The corresponding dispatch branch goes in `execute_tool()` in the same file. Keep tool names snake_case and match them exactly between the schema and dispatch.

## Skill Routing

Router calls must use `response_format={"type": "json_object"}` so the response is always parseable. If the router call fails for any reason, fall back to loading all enabled skills — never block the request.
