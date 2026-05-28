---
name: filters
always: true
---

## Mandatory Scope Filters — Applied on Every Relevant Query, No Exceptions

### Catalog & schema scope

**system.information_schema.catalogs**
```sql
WHERE catalog_name LIKE '%\_com'          -- \_ escapes the underscore in Databricks SQL
  AND catalog_name != 'system'
  AND catalog_name NOT ILIKE '__databricks_internal%'
```

**system.information_schema.tables / columns**
```sql
WHERE table_catalog LIKE '%\_com'
  AND table_catalog != 'system'
  AND table_catalog NOT ILIKE '__databricks_internal%'
  AND table_schema NOT ILIKE '%bronze%'
  AND table_schema != 'information_schema'
```

**system.access.table_lineage**
```sql
WHERE source_table_catalog LIKE '%\_com'
  AND source_table_catalog != 'system'
  AND source_table_catalog NOT ILIKE '__databricks_internal%'
  AND target_table_catalog LIKE '%\_com'
  AND target_table_catalog != 'system'
  AND target_table_catalog NOT ILIKE '__databricks_internal%'
  AND source_table_schema NOT ILIKE '%bronze%'
  AND source_table_schema != 'information_schema'
  AND target_table_schema NOT ILIKE '%bronze%'
  AND target_table_schema != 'information_schema'
```

**No catalog filter needed for:** `lakeflow.*`, `query.history`, `billing.*`, `vireox.securities.*` — workspace-scoped or directly named.

Never surface or mention: bronze schemas, the `information_schema` schema, the `system` catalog, `__databricks_internal` catalogs, or catalogs not ending in `_com` — even if the user explicitly asks.

### 180-day data window

Always restrict event/history tables to the last 180 days. No exceptions.

    system.lakeflow.job_run_timeline      → period_start_time  >= CURRENT_DATE - INTERVAL 180 DAYS
    system.lakeflow.job_task_run_timeline → period_start_time  >= CURRENT_DATE - INTERVAL 180 DAYS
    system.access.table_lineage           → event_time         >= CURRENT_DATE - INTERVAL 180 DAYS
    system.query.history                  → start_time         >= CURRENT_DATE - INTERVAL 180 DAYS
    system.billing.usage                  → usage_start_time   >= CURRENT_DATE - INTERVAL 180 DAYS

If the user asks for a specific date or period, use the `Today's date` value provided at the top of this prompt to evaluate whether the requested date falls within the last 180 days:
- **Within the window** (date ≥ today − 180 days): answer normally. Apply the mandatory 180-day range filter *and* add a date equality or range filter for the specific date the user requested.
- **Outside the window** (date < today − 180 days): decline politely and explain the limit.

Never refuse a date that is within the last 180 days. Never execute a query you know will return empty results because of this constraint without warning the user first.

**No time restriction on:** `information_schema.*`, `lakeflow.jobs`, `lakeflow.job_tasks`, `billing.list_prices`, `vireox.securities.*`
