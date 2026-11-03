# Architecture Rules

## Service Boundaries

**Receiver** (`src/receiver/`) does exactly two things: verify the Slack signature and enqueue the job. Never add agent logic, Databricks calls, or Supabase reads to this service.

Exceptions, both narrowly scoped to `/slack/interactivity`:
- Its `block_actions` handler calls `views.open` directly instead of enqueuing. Slack's `trigger_id` expires ~3 seconds after issuance — a window the RQ queue can't reliably guarantee under load — and the view is built by a pure function (`agent.forms.access_request_view.build_access_request_view`) from data already carried in the button's `value`, so no extra Supabase/Databricks I/O is introduced.
- Its `view_submission` handler runs `agent.forms.access_request_view.validate_submission()` — pure regex format validation, no I/O — synchronously before enqueuing, because Slack requires field errors on the view_submission response itself (`response_action: "errors"`) before the modal closes; an async worker reply would arrive after the modal is already gone. Anything requiring real I/O (the Databricks principal lookup) still happens in the worker task after enqueuing.

A third exception, unrelated to Slack payloads entirely: `GET /secrets/{token}` (alongside `/healthz`) serves a one-time service-principal-secret view via an atomic Redis `GETDEL` — no Slack signature to verify (it's opened directly in a browser by a human, not called by Slack) and no Supabase/Databricks I/O (the secret is already sitting in Redis by the time the link is posted). See "Service Principal Secrets" below.

**Worker** (`src/worker/`) is a thin RQ shell: `tasks.py` loads the bot config, handles Slack-side UX (thinking animation, reply chunking), and calls into `src/agent/`. It owns no agent logic of its own, must not listen on any port, and must not call Slack Event API endpoints directly. The RQ job paths (`worker.tasks.<fn>`) are strings enqueued by the receiver and scheduler — renaming a task function or module breaks jobs already in the queue.

**Agent** (`src/agent/`) owns all agent logic, organised by task:
- `agent.py` (loop), `dispatch.py` (tool routing), `history.py` (thread history in Redis)
- `skills/` — loader, router, and the skill files in `skills/files/`
- `forms/` — Slack modal view, submission validation, `VerifiedAccessRequest` (pure, no I/O)
- `pr/` — `rules_v2.yaml`/PR-template rendering (pure) and `data_platform_repo.py` (the access-control branch/PR workflow on top of `connectors.github`)
- `access_control/` — access-request categories and requests (Supabase), principal lookups, submission, review, and the `request_data_access` tool
- `service_principal_secrets/` — encrypted secret cache and the `generate_service_principal_secret` tool
- `schedules.py` — the schedule-CRUD tool handlers

**Connectors** (`src/connectors/`) are generic clients for external tools (Slack, Databricks, GitHub, OpenAI, Redis, Supabase connection/bots/schedules). They expose raw primitives and carry no task-specific logic — anything that knows about access requests, rules files, or skills belongs in `src/agent/`. `db/schedules.py` stays here because the scheduler service reads it directly.

**Scheduler** (`src/scheduler/`) only evaluates cron schedules and enqueues jobs. It must not run the agent itself or post to Slack directly.

Services communicate exclusively through the Redis `slack_events` RQ queue. Never import across service boundaries (receiver ↔ worker ↔ scheduler). `agent/` and `connectors/` are shared libraries, not services; the receiver may import only the pure, I/O-free `agent.forms` (and must not import anything from `agent/` that pulls in OpenAI, Redis or Supabase — keep `agent/__init__.py` and `agent/forms/__init__.py` empty).

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
| `secret_link:{token}` | `AgentConfigs.SECRET_LINK_TTL_SECONDS` (600 s) | One-time service-principal-secret view link; read via atomic `GETDEL` in `receiver/app.py`, so it's also deleted the instant it's read |

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
- The `generate_service_principal_secret` tool (`category="secrets"`) is gated by **both** channel and reviewer membership from `access_request_categories` (`request_type="service_principal_secret"`) — unlike `data_access`, the requester themselves must be a listed reviewer; there's no separate approval step
- Authorization status (is_admin) is prepended to the question text, not stored in history

## Adding Tools to the Agent

New OpenAI tool schemas go in `AgentConfigs.TOOLS` (`src/common/configs.py`) as an `AgentTool(...)` entry with a `category` (`"core"`, `"schedule"`, `"data_access"`, or `"secrets"`). The corresponding dispatch branch goes in `dispatch_tool()` in `src/agent/dispatch.py` (which routes by category to `dispatch_schedule_tool()` in `agent/schedules.py` / `dispatch_data_access_tool()` in `agent/access_control/tools.py` / `dispatch_secret_generation_tool()` in `agent/service_principal_secrets/tools.py` / inline core handling). OpenAI calls go through `src/connectors/openai_client.py`. Keep tool names snake_case and match them exactly between the `AgentTool` entry and the dispatch branch.

## Skill Routing

Router calls must use `response_format={"type": "json_object"}` so the response is always parseable. If the router call fails for any reason, fall back to loading all enabled skills — never block the request.

## Data Access Requests

A parallel, mostly-deterministic flow alongside the main agent loop, for requesting and approving access to Databricks tables/row filters:

1. **Request** — the `request_data_access` tool (`dispatch_data_access_tool` in `src/agent/access_control/tools.py`) checks the `access_request_categories` table for the bot/channel/request_type, then posts a Slack button. The button's `value` carries the request metadata (including `reviewers` and an `expires_at`) — nothing is written to Supabase yet.
2. **Form** — `block_actions`/`view_submission` on `/slack/interactivity` are the receiver's two documented exceptions above; the actual insert happens after enqueuing, in `worker.tasks.process_data_access_submission` (which calls `agent.access_control.submission.submit_data_access_request`), which writes a `pending` row to `access_requests` (`src/agent/access_control/access_requests.py`) with a 24 h `expires_at`.
3. **Review** — a reviewer replies `approve <id>` / `reject <id>` in the thread. `reply_to_mention` (`src/worker/tasks.py`) detects this prefix and routes to `agent/access_control/review.py::handle_review_decision` (after `parse_review_command` recognises the prefix) **before** the LLM agent loop runs — approval/rejection is exact-keyword control flow, not an LLM tool call. It re-validates channel eligibility and reviewer membership (`user_id in row.reviewers`) at approval time, not just at submission time.
4. **Approval** — resolves the principal via Databricks (`agent/access_control/principals.py` or `service_principals.py`, on top of `connectors/databricks/scim.py`), renders the `rules_v2.yaml`/`service_principals.yaml` snippets (`src/agent/pr/access_control_rules.py`), and opens (or reuses) a PR on `100xteam-ai/vireox-data-platform` via `src/agent/pr/data_platform_repo.py` (on the generic `src/connectors/github.py`) — one branch/PR per `ticket_id`, check-then-create so retries and duplicate approvals never create duplicate branches, files, or PRs. The bot's approval only opens the PR — it does not merge it. `handle_review_decision`'s reply (`src/agent/access_control/review.py::_approve`) tells the reviewer that merging the PR on GitHub is the remaining step: merging to the base branch triggers `vireox-data-platform`'s own CI/CD, which deploys the change to production (GA) automatically.

**GitHub client** (`src/connectors/github.py`) follows the same credential rule as the Databricks client: the credentials (GitHub App id/private key, preferred, or the PAT `GIT_REPO_PAT_DATA_PLATFORM`) come from the env, never from Supabase.

## Service Principal Secrets

A synchronous, single-turn flow (no form, no separate approval step — unlike Data Access Requests above) for reviewers to self-serve a Databricks service-principal OAuth client secret:

1. **Dispatch** — `generate_service_principal_secret(service_account)` (`dispatch_secret_generation_tool` in `src/agent/service_principal_secrets/tools.py`) first validates `service_account` against the same `EMAIL_PATTERN` regex used by `agent/forms/access_request_view.py`, then checks the `access_request_categories` table (`request_type="service_principal_secret"`) for **both** channel eligibility and that the requesting user is a listed reviewer — refusing immediately if any of these fail.
2. **Lookup** — verifies the service principal exists via `agent/access_control/principals.py::find_service_principal_by_email`; refuses politely if not found. No Databricks/Supabase writes happen before this point.
3. **Cache check** — `agent/service_principal_secrets/store.py::get` looks up a cached row (keyed by `email`, the bare address, which is the table's unique constraint — not scoped by `bot_id`, since the same service principal can be requested via more than one bot; and not `client_id`, since a service principal can be deleted and recreated for the same user, getting a new `client_id` in the process). Databricks only ever returns a secret's plaintext once, at creation, so this table stores it encrypted (`common/crypto.py`, Fernet, key from `Configs.SECRET_ENCRYPTION_KEY`) to make "return the existing secret if still valid" possible at all. If the cached row is `status == "ACTIVE"` and unexpired, it's decrypted and reused — no new Databricks secret is minted (Databricks caps active secrets per service principal).
4. **Mint** — otherwise, `connectors/databricks/service_principal_secrets.py::create_secret` requests a new one; the flattened response is cached via `upsert` (encrypted) for next time.
5. **Hand-off** — a fresh random token is written to Redis (`secret_link:{token}`, see the Redis table above) holding the **full flattened record** (`id`, `secret`, `secret_hash`, `status`, `create_time`, `update_time`, `expire_time`, `service_account`, `client_id`, `email` — the same shape Databricks itself returns) as JSON, with a short TTL, and a plain link (no button) is posted to Slack. `GET /secrets/{token}` on the receiver (documented exception above) serves it exactly once via atomic `GETDEL`, then it's gone — this is deliberately re-generated on every request (cache-hit or not) so a cached-valid secret still gets its own fresh, single-use link rather than reusing a stale one.

Out of scope for now: revoking the previous Databricks secret on rotation (a `delete()` method exists on the same proxy API if this becomes necessary), and reconciling the cached `status` against a live Databricks listing (an invalid/expired cache entry simply triggers a fresh mint).
