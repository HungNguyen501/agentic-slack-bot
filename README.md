# Databricks Assistant — Slack Bot

An agentic Slack bot that answers questions about your Databricks data infrastructure — catalogs, schemas, tables, columns, jobs, lineage, query history, and usage costs.

Powered by Chat GPT with function calling. Queries Databricks system tables on demand and replies in-thread with full context of the conversation.

## Architecture

![workflow](./docs/workflow.jpg)

## What it can answer

- How many catalogs / schemas / tables exist
- Column definitions and data types for a table
- Databricks job configs, schedules, and run history
- Data lineage — upstream and downstream table dependencies
- Query execution history — who ran what, when, duration, status
- Platform DBU consumption and estimated cost by workspace, SKU, or user

Questions about business data values (revenue, customer counts, etc.) are out of scope and politely declined.

## Setup
### 1. Fill in `.env`

```bash
$ cp .env.example .env
# Fill in all values
```

Set `WORKER_COUNT` to the number of concurrent workers you want (default: `2`).

### 2. Start services

```bash
$ docker compose up --build
```

### 3. Finish Slack setup

Open http://localhost:4040 → copy the `https://...ngrok-free.app` URL.

Back in Slack app config:
- **Event Subscriptions** → toggle **On**
- **Request URL**: `https://<your-ngrok>.ngrok-free.app/slack/events`
- Slack pings the URL; receiver responds to the challenge → ✅ Verified
- **Subscribe to bot events** → add `app_mention`
- **Save Changes** → reinstall the app if prompted.

### 4. Test

Invite the bot to a channel: `/invite @yourbot`

Try asking:
```
@yourbot how many tables are in the gold schema?
@yourbot what columns does the orders table have?
@yourbot show me failed job runs in the last 7 days
```

Follow-up questions work — the bot remembers the thread conversation for 24 hours.

## Scaling workers

Set `WORKER_COUNT` in `.env` and restart:

```bash
$ docker compose up -d
```

Each worker container handles one question at a time. Redis distributes jobs to the first free worker (competing-consumer), so load is spread evenly without any round-robin configuration.

## Updating the agent instructions

Edit [`worker/system_prompt.md`](worker/system_prompt.md) directly — no code changes needed. Rebuild the image to apply:

```bash
docker compose up --build -d
```
