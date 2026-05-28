---
name: billing
always: false
description: Use when the question is about DBU consumption, cost by workspace or user or SKU, query execution history, who ran which queries, query duration, warehouse usage, or estimated spend.
---

## Skill: Usage & Observability

*No catalog/schema filter required. 180-day filter applies to event tables.*

**system.query.history** *(180-day filter on `start_time`)*
- Columns: `account_id`, `workspace_id`, `statement_id`, `session_id`, `executed_by`, `executed_by_user_id`, `executed_as`, `executed_as_user_id`, `statement_text`, `execution_status` (FINISHED / FAILED / CANCELED), `statement_type`, `start_time`, `end_time`, `update_time`, `total_duration_ms`, `execution_duration_ms`, `compilation_duration_ms`, `waiting_for_compute_duration_ms`, `waiting_at_capacity_duration_ms`, `total_task_duration_ms`, `result_fetch_duration_ms`, `compute` (struct: `compute.type`, `compute.cluster_id`), `error_message`, `client_application`, `client_driver`, `read_rows`, `produced_rows`, `read_bytes`, `written_bytes`, `written_rows`, `from_result_cache`, `query_source` (struct), `query_tags`
- The status column is `execution_status` — **not** `status`. Using `status` will cause an unresolved column error.
- Warehouse/SQL queries: use `compute.cluster_id` to identify the warehouse

**system.billing.usage** *(180-day filter on `usage_start_time`)*
- Columns: `record_id`, `account_id`, `workspace_id`, `sku_name`, `cloud`, `usage_start_time`, `usage_end_time`, `usage_date`, `usage_quantity`, `usage_unit`, `usage_metadata` (struct: `warehouse_id`, `job_id`, `job_run_id`, `cluster_id`, `notebook_id`, `dlt_pipeline_id`, `node_type`), `identity_metadata` (struct: `run_as`, `owned_by`, `created_by`), `custom_tags`, `record_type`, `billing_origin_product`, `product_features`, `usage_type`
- `identity_metadata.run_as` — direct user/service principal attribution for job and notebook workloads; use this for per-user job cost without complex joins
- `usage_metadata.warehouse_id` — use to link SQL warehouse usage to `query.history`
- `record_type`: ORIGINAL (normal), RETRACTION, RESTATEMENT — filter `record_type = 'ORIGINAL'` for standard cost queries

**system.billing.list_prices** *(no time restriction)*
- Columns: `sku_name`, `cloud`, `currency_code`, `usage_unit`, `price_start_time`, `price_end_time` (NULL = currently active), `pricing.default` (decimal — list price per unit)

**Standard cost pattern (usage × list price):**
```sql
SELECT u.workspace_id, u.sku_name,
       SUM(u.usage_quantity)                        AS total_dbus,
       SUM(u.usage_quantity * p.pricing.default)    AS estimated_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON  u.sku_name          = p.sku_name
 AND  u.cloud             = p.cloud
 AND  u.usage_start_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.usage_start_time >= CURRENT_DATE - INTERVAL 180 DAYS
  AND u.record_type = 'ORIGINAL'
GROUP BY u.workspace_id, u.sku_name
ORDER BY estimated_cost_usd DESC
```

**Per-user cost — jobs and notebooks (direct attribution):**
For job and notebook workloads, `identity_metadata.run_as` directly identifies the user. This is the preferred method — no approximation needed.

```sql
SELECT u.identity_metadata.run_as AS user,
       u.billing_origin_product,
       SUM(u.usage_quantity * p.pricing.default) AS estimated_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON  u.sku_name          = p.sku_name
 AND  u.cloud             = p.cloud
 AND  u.usage_start_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.usage_start_time >= CURRENT_DATE - INTERVAL 180 DAYS
  AND u.record_type = 'ORIGINAL'
  AND u.identity_metadata.run_as IS NOT NULL
GROUP BY 1, 2
ORDER BY estimated_cost_usd DESC
```

**Per-user cost — shared SQL warehouses (proportional attribution):**
SQL warehouse usage has no direct user column. Approximate by attributing cost proportional to each user's query duration share on the warehouse. Always disclose this is an estimate.

```sql
WITH warehouse_dbus AS (
    SELECT usage_metadata.warehouse_id AS warehouse_id,
           usage_start_time, usage_end_time,
           usage_quantity * p.pricing.default AS period_cost_usd
    FROM system.billing.usage u
    JOIN system.billing.list_prices p
      ON  u.sku_name          = p.sku_name AND u.cloud = p.cloud
     AND  u.usage_start_time >= p.price_start_time
     AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
    WHERE u.usage_start_time >= CURRENT_DATE - INTERVAL 180 DAYS
      AND u.record_type = 'ORIGINAL'
      AND usage_metadata.warehouse_id IS NOT NULL
),
query_durations AS (
    SELECT executed_by,
           compute.cluster_id             AS warehouse_id,
           DATE_TRUNC('hour', start_time) AS hour_bucket,
           SUM(total_duration_ms)         AS user_ms
    FROM system.query.history
    WHERE start_time >= CURRENT_DATE - INTERVAL 180 DAYS
    GROUP BY 1, 2, 3
),
window_totals AS (
    SELECT compute.cluster_id             AS warehouse_id,
           DATE_TRUNC('hour', start_time) AS hour_bucket,
           SUM(total_duration_ms)         AS total_ms
    FROM system.query.history
    WHERE start_time >= CURRENT_DATE - INTERVAL 180 DAYS
    GROUP BY 1, 2
)
SELECT q.executed_by,
       SUM(d.period_cost_usd * q.user_ms / NULLIF(t.total_ms, 0)) AS estimated_cost_usd
FROM query_durations q
JOIN window_totals  t ON q.warehouse_id = t.warehouse_id AND q.hour_bucket = t.hour_bucket
JOIN warehouse_dbus d ON q.warehouse_id = d.warehouse_id
                      AND q.hour_bucket >= DATE_TRUNC('hour', d.usage_start_time)
                      AND q.hour_bucket <  DATE_TRUNC('hour', d.usage_end_time)
GROUP BY q.executed_by
ORDER BY estimated_cost_usd DESC
```
