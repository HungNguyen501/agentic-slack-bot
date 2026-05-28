---
name: access
always: false
description: Use when the question is about which tables a user can access, which users can see a specific table, tag-based permissions, access control rules, or GPT/AI platform onboarding.
---

## Skill: Data Access Control

*No time restriction. Configuration tables.*

These two tables work as a pair: `gpt_access_tables` defines which tables exist and what tag is required; `access_control_rules_ver2` defines which users/groups hold which tags.

**vireox.securities.gpt_access_tables**
- Columns: `table` (fully-qualified `catalog.schema.table`), `tag_name`, `table_type` (VIEW / MANAGED / METRIC_VIEW), `table_owner`, `created`, `created_by`, `last_altered`, `last_altered_by`
- `tag_name = 'public'` → accessible to all authenticated users without a tag restriction

**vireox.securities.access_control_rules_ver2**
- Columns: `user_email` (email — used across all principal types: USER, bot, and Service principals), `principal_type` (USER / Service principals / GROUP), `group_type` (ACCOUNT / WORKSPACE / NULL), `filter_column`, `allowed_value`, `scope_column`, `scope_value`, `tags` (ARRAY<STRING>)
- Two independent OR access paths:
  - Column-level: a row is visible when `filter_column = allowed_value` for that principal. `allowed_value = 'ALL'` grants unrestricted column access.
  - Tag-level: access granted when the principal's `tags` array intersects the table's `tag_name`. `allowed_value = '__vx_skip'` marks a tag-only rule (no column restriction).
- `scope_column` + `scope_value` (e.g. `company_key = 'vireohealth_com'`) enforce tenant isolation — a rule scoped to one company never applies to another

**What can a user access:**
When the question is about the asking user's own access, use the `user_email` value provided at the top of each conversation. All principal types (USER, bot, Service principals) are identified by email in the `user_email` column — do not filter by `principal_type`.

```sql
-- Step 1: retrieve the user's rules (all principal types, matched by email)
SELECT user_email, principal_type, filter_column, allowed_value,
       scope_column, scope_value, tags
FROM vireox.securities.access_control_rules_ver2
WHERE user_email ILIKE '{user_email}'

-- Step 2: resolve accessible tables via tag
SELECT g.table, g.tag_name, g.table_type
FROM vireox.securities.gpt_access_tables g
WHERE g.tag_name = 'public'
   OR EXISTS (
       SELECT 1
       FROM vireox.securities.access_control_rules_ver2 r
       WHERE r.user_email ILIKE '{user_email}'
         AND array_contains(r.tags, g.tag_name)
   )
```

**Who can access a specific table:**
```sql
SELECT r.user_email, r.principal_type, r.scope_value, r.tags
FROM vireox.securities.gpt_access_tables g
JOIN vireox.securities.access_control_rules_ver2 r
  ON array_contains(r.tags, g.tag_name)
WHERE g.table ILIKE '<catalog.schema.table>'
```

**User not found:** If a principal does not appear in `access_control_rules_ver2`, this is NOT an error and does not mean no access. It means the person is either an internal Vireox member managed outside this table, or has not yet been onboarded to the GPT/AI platform. Never say the user "has no access" or imply something is broken.
