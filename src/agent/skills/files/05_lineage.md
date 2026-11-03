---
name: lineage
always: false
description: Use when the question is about data lineage, upstream or downstream table dependencies, which jobs or notebooks write to a table, or what tables a pipeline reads from.
---

## Skill: Data Lineage

*180-day filter on `event_time`. Apply full catalog + schema scope filter.*

**system.access.table_lineage**
- Columns: `account_id`, `workspace_id`, `metastore_id`, `entity_type` (NOTEBOOK / JOB / PIPELINE / DASHBOARD_V3 / DBSQL_DASHBOARD / DBSQL_QUERY), `entity_id`, `entity_run_id`, `source_table_full_name`, `source_table_catalog`, `source_table_schema`, `source_table_name`, `source_path`, `source_type` (TABLE / VIEW / PATH / MATERIALIZED_VIEW / STREAMING_TABLE), `target_table_full_name`, `target_table_catalog`, `target_table_schema`, `target_table_name`, `target_path`, `target_type`, `direct_access`, `created_by`, `event_time`, `event_date`, `record_id`, `event_id`, `statement_id`, `entity_metadata` (struct)
- Use `source_table_full_name` / `target_table_full_name` for equality filters
- `direct_access = true` — source directly referenced; false — indirect lineage
- This table has one row per access event. Use `SELECT DISTINCT source_table_full_name, target_table_full_name` (or `GROUP BY`) when counting or listing unique relationships — never count raw rows as relationship counts
