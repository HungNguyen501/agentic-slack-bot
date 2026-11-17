# Skill Authoring Rules

These rules cover the **agent** skills in `src/agent/skills/files/` only. The Claude Code dev skills in `.claude/skills/` are hub-managed and follow their own format.

## File Naming

Skills live in `src/agent/skills/files/` and are named `NN_name.md` where `NN` is a zero-padded two-digit sequence number. The number controls load order. Pick the next available number.

Current skills:
- `01_core.md` — identity, scope, query rules (always-loaded)
- `02_filters.md` — catalog/time scope (always-loaded)
- `03_metadata.md` — information_schema
- `04_jobs.md` — Lakeflow job configs & run history
- `05_lineage.md` — table lineage
- `06_billing.md` — DBU billing & query history
- `07_access.md` — data access control
- `08_formatting.md` — Slack mrkdwn rules (always-loaded)
- `09_schedules.md` — schedule CRUD + admin checks
- `10_semantic.md` — semantic metric views
- `11_data_access_requests.md` — `request_data_access` tool usage (submitting a new request; not the reviewer approve/reject flow, which bypasses skills/routing entirely — see architecture.md "Data Access Requests")
- `12_service_principal_secrets.md` — `generate_service_principal_secret` tool usage (see architecture.md "Service Principal Secrets")
- `13_pr_reminders.md` — `check_pr_approvals` / `schedule_pr_reminder` tool usage (see architecture.md "PR Review Reminders")
- `14_dataset_sync.md` — `list_synced_datasets` / `sync_dataset` / `schedule_dataset_sync` tool usage (see architecture.md "Synced Datasets")

## Required Frontmatter

Every skill file must start with:

```yaml
---
name: <kebab-case-unique-name>
always: <true|false>
description: <one sentence starting with "Use when...">
---
```

- `name` must be unique across all skills; it's used for per-bot `enabled_skills` allowlisting
- `always: true` loads the skill on every request regardless of routing
- `description` is the only text the router model sees when selecting skills — write it precisely

## Content Guidelines

- Write SQL examples as fenced code blocks with the `sql` language tag
- Reference actual table/column names from the Databricks `system` catalog or `information_schema`
- Include the exact column names the agent should SELECT, not just prose descriptions
- Keep skills focused on one domain area; do not combine unrelated query domains

## Routing Behavior

- Router model is `gpt-6.1-sol` by default (`ROUTER_MODEL` env var)
- Router receives: skill descriptions + the user's question + last few conversation turns
- If the router errors, all enabled skills are loaded (fail-open)
- Per-bot `enabled_skills` array in Supabase gates which skills are available; empty array = all skills enabled

## Updating Skills

Git is the source of truth, but running services read only the Supabase `agent_skills` table (`agent/skills/loader.py`), one SELECT per request. There is no fallback to the files in the image: if the table is empty the request fails, so the table must be synced before a deploy that needs it.

- **Deployed:** merge to `master` — the *DB Migrate* workflow runs `make skills-sync` on `development`, then `staging`, replacing the table with the files (deleting rows whose file is gone). No image tag, no redeploy. Revert the commit to roll back.
- **Local:** edit the file, then run `make skills-sync` with `SUPABASE_DB_URL` pointing at the database your local services use. The edit applies on the next request. (docker-compose no longer mounts the skills folder.) Use your own Supabase project for this: a sync replaces the whole table, so running it against the shared `development` database changes the skills for everyone there until CI's next sync.
- The sync refuses to run with no files, a duplicate `name`, a file without a frontmatter `name`, or a routed (`always: false`) skill without a `description`.
