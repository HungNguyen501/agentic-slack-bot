# /add-skill

Scaffold a new **agent** skill file in `src/agent/skills/files/` (not a Claude Code skill in `.claude/skills/`).

## Steps

1. List existing skills to find the next sequence number:
   ```bash
   ls src/agent/skills/files/
   ```

2. Create `src/agent/skills/files/NN_<name>.md` with this template:
   ```markdown
   ---
   name: <name>
   always: false
   description: Use when the user asks about <domain>.
   ---

   ## <Domain Name>

   <Describe the Databricks tables or views this skill covers, with SQL examples.>
   ```

3. Verify the frontmatter is valid YAML and `name` is unique across all skill files.

4. If the new skill should be available only to specific bots, note that the `enabled_skills` array in the Supabase `bots` table must be updated for those bots.

5. No image rebuild needed, but the file alone does nothing: services read the Supabase `agent_skills` table. Merging to `master` syncs it (CI runs `make skills-sync` after the migrations); to try it earlier, run `make skills-sync` against your own database.

## Example

To add a skill for Unity Catalog tag management:

```bash
# next number after 11_data_access_requests.md is 12
touch src/agent/skills/files/12_tags.md
```

```markdown
---
name: tags
always: false
description: Use when the user asks about Unity Catalog tags, tag assignments, or tagged assets.
---

## Catalog Tags

List all tags:
...
```
