---
name: billing
always: false
description: Use when the question is about individual query text, who ran a specific query, raw query history lookup, warehouse event details, or when you need the raw system.billing or system.query.history table columns for a non-aggregation query. For aggregated cost totals, rates, and trends use the semantic skill instead.
---

## Skill: Raw Usage & Query Tables

*No catalog/schema filter required. 180-day filter applies to event tables.*

> **Aggregation questions (cost totals, DBU by user/product, query success rates, p95 latency):**
> Use `vireox_infra.semantic.vw_dbu_cost` or `vireox_infra.semantic.vw_query_perf` from the **semantic** skill.
> These views pre-apply the list-price JOIN and flag columns — do not write the raw JOIN pattern below for aggregation.

---

### system.query.history *(180-day filter on `start_time`)*

Use for: looking up individual query text, specific statement IDs, error messages, or queries by a specific user.

- Key columns: `workspace_id`, `statement_id`, `executed_by`, `executed_as`, `statement_text`, `execution_status` (FINISHED / FAILED / CANCELED), `statement_type`, `start_time`, `end_time`, `total_duration_ms`, `execution_duration_ms`, `compilation_duration_ms`, `waiting_for_compute_duration_ms`, `waiting_at_capacity_duration_ms`, `total_task_duration_ms`, `result_fetch_duration_ms`, `error_message`, `client_application`, `read_rows`, `produced_rows`, `read_bytes`, `written_bytes`, `from_result_cache`, `query_tags`
- `compute` struct: `compute.type` (WAREHOUSE or SERVERLESS_COMPUTE), `compute.cluster_id` (warehouse ID), `compute.warehouse_id`
- `query_source` struct: `query_source.job_info.job_id`, `query_source.dashboard_id`, `query_source.notebook_id`, `query_source.genie_space_id`
- **`execution_status` is the correct column — never use `status`.**
- Warehouse queries: identify the warehouse via `compute.cluster_id`

---

### system.billing.usage *(180-day filter on `usage_start_time`)*

Use for: raw record-level inspection, non-standard filtering, or joins the semantic view cannot express.

- Key columns: `record_id`, `workspace_id`, `sku_name`, `cloud`, `usage_start_time`, `usage_end_time`, `usage_date`, `usage_quantity`, `usage_unit`, `billing_origin_product`, `usage_type`, `record_type`
- `usage_metadata` struct: `.warehouse_id`, `.job_id`, `.job_run_id`, `.job_name`, `.cluster_id`, `.notebook_id`, `.dlt_pipeline_id`, `.app_id`, `.app_name`, `.node_type`, `.endpoint_name`, `.endpoint_id`
- `identity_metadata` struct: `.run_as` (executing user/SP), `.owned_by` (warehouse owner), `.created_by` (app creator)
- `product_features` struct: `.jobs_tier`, `.sql_tier`, `.is_serverless`, `.is_photon`, `.serving_type`
- `record_type`: ORIGINAL (standard), RETRACTION, RESTATEMENT — filter `record_type = 'ORIGINAL'` always

---

### system.billing.list_prices *(no time restriction)*

Use only when building custom cost calculations the semantic view cannot cover.

- Columns: `sku_name`, `cloud`, `currency_code`, `usage_unit`, `price_start_time`, `price_end_time` (NULL = currently active), `pricing.default` (list price per unit)
- Join condition (time-range overlap — **mandatory**):
  ```sql
  ON  u.sku_name          = p.sku_name
  AND u.cloud             = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  ```
