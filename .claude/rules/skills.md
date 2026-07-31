# Skill Authoring Rules

## File Naming

Skills live in `src/worker/skills/` and are named `NN_name.md` where `NN` is a zero-padded two-digit sequence number. The number controls load order. Pick the next available number.

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

- Router model is `gpt-4o-mini` by default (`ROUTER_MODEL` env var)
- Router receives: skill descriptions + the user's question + last few conversation turns
- If the router errors, all enabled skills are loaded (fail-open)
- Per-bot `enabled_skills` array in Supabase gates which skills are available; empty array = all skills enabled

## Updating Skills

Skills are mounted as a volume in docker-compose (`./src/worker/skills:/app/src/worker/skills`). Editing a skill file takes effect on the next request without rebuilding the image.
