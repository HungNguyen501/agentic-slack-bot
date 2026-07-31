# /add-skill

Scaffold a new skill file for the agent.

## Steps

1. List existing skills to find the next sequence number:
   ```bash
   ls src/worker/skills/
   ```

2. Create `src/worker/skills/NN_<name>.md` with this template:
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

5. No image rebuild needed — skills are volume-mounted in docker-compose.

## Example

To add a skill for Unity Catalog tag management:

```bash
# next number after 10_semantic.md is 11
touch src/worker/skills/11_tags.md
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
