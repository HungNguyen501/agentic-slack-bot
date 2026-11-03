---
name: semantic
always: false
description: Use when the question asks for aggregated metrics, rates, trends, or totals: DBU cost by user/workspace/product, job success or failure rates, job duration statistics (avg/p95), query performance trends, cache hit rates, or any "how much / how many / what percentage / what is the trend" question about Databricks operations. Prefer this skill over billing or jobs for aggregation questions.
---

## Skill: Semantic Metric Views

Use the three Metric Views below for all **aggregation** questions (totals, rates, averages, percentiles, trends). They pre-resolve complex joins, SCD2 deduplication, and flag columns so the agent generates short, error-free SQL.

**When to use Metric Views vs. raw system tables:**

| Question type | Use |
|---|---|
| "How much did X cost last month?" | `vw_dbu_cost` |
| "Which user spent the most DBUs?" | `vw_dbu_cost` |
| "What is the success rate for job Y?" | `vw_job_run_stats` |
| "Show me p95 job duration this week" | `vw_job_run_stats` |
| "How many queries failed on warehouse Z?" | `vw_query_perf` |
| "What is the cache hit rate today?" | `vw_query_perf` |
| "Which runs failed yesterday — show run IDs and termination codes" | raw `system.lakeflow.job_run_timeline` (individual rows) |
| "What tables exist in schema X?" | raw `system.information_schema.tables` |
| "Show lineage for table Y" | raw `system.access.table_lineage` |

---

## vw_dbu_cost — DBU Consumption & Estimated Cost

**Location:** `vireox_infra.semantic.vw_dbu_cost`
**Source:** `system.billing.usage × system.billing.list_prices` (join pre-applied; `record_type = 'ORIGINAL'` pre-filtered)

**Always filter by `usage_date` or `usage_start_time` within the 180-day window.**

**Fields (GROUP BY / WHERE)**

| Field | Description |
|---|---|
| `workspace_id` | Workspace identifier |
| `billing_origin_product` | JOBS, SQL, DLT, MODEL_SERVING, NOTEBOOKS, INTERACTIVE, etc. |
| `sku_name` | Billing SKU name |
| `run_as` | User or service principal attributed to the workload (NULL for shared SQL warehouses) |
| `job_name` | Job name for job workloads (no join needed) |
| `warehouse_id` | SQL warehouse ID |
| `job_id` | Lakeflow job ID |
| `usage_date` | Calendar date (use for daily/monthly GROUP BY) |
| `usage_start_time` | Billing period start timestamp |

**Measures (SELECT)**

| Measure | Description |
|---|---|
| `total_dbus` | SUM of DBU consumption |
| `estimated_cost_usd` | SUM of cost at list prices |
| `avg_daily_cost_usd` | Cost ÷ distinct days in the window |

**Example — cost by user this month:**
```sql
SELECT run_as, SUM(estimated_cost_usd) AS cost_usd, SUM(total_dbus) AS dbus
FROM vireox_infra.semantic.vw_dbu_cost
WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
  AND run_as IS NOT NULL
GROUP BY run_as
ORDER BY cost_usd DESC
LIMIT 20
```

**Example — cost by product last 30 days:**
```sql
SELECT billing_origin_product,
       SUM(estimated_cost_usd) AS cost_usd,
       SUM(total_dbus)         AS dbus
FROM vireox_infra.semantic.vw_dbu_cost
WHERE usage_date >= CURRENT_DATE - INTERVAL 30 DAYS
GROUP BY billing_origin_product
ORDER BY cost_usd DESC
```

**Note on SQL warehouse user attribution:** `run_as` is NULL for shared SQL warehouse usage. For per-user warehouse cost estimates use `vw_query_perf` to get each user's query duration share, then join proportionally to cost — and always disclose it is an estimate.

---

## vw_job_run_stats — Job Run Outcomes & Durations

**Location:** `vireox_infra.semantic.vw_job_run_stats`
**Source:** `system.lakeflow.job_run_timeline` joined to `system.lakeflow.jobs` (SCD2 dedup and `result_state IS NOT NULL` pre-applied)

**Always filter by `run_date` or `period_start_time` within the 180-day window.**

**Fields (GROUP BY / WHERE)**

| Field | Description |
|---|---|
| `job_name` | Job name (already resolved — no join needed) |
| `job_id` | Use for constructing run URLs |
| `run_id` | Individual run identifier |
| `creator_user_name` | User who created the job |
| `run_as_user_name` | Identity the job runs as |
| `trigger_type` | SCHEDULED, MANUAL, FILE_ARRIVAL, CONTINUOUS |
| `run_type` | JOB_RUN, SUBMIT_RUN, WORKFLOW_RUN |
| `result_state` | SUCCEEDED, FAILED, CANCELLED, TIMED_OUT, SKIPPED, ERROR, BLOCKED |
| `termination_code` | Detailed termination reason |
| `run_date` | Calendar date (use for daily GROUP BY) |
| `period_start_time` | Run start timestamp |

**Measures (SELECT)**

| Measure | Description |
|---|---|
| `total_runs` | COUNT of completed runs |
| `succeeded_runs` | Runs with SUCCEEDED state |
| `failed_runs` | Runs with FAILED state |
| `cancelled_runs` | Runs with CANCELLED state |
| `timed_out_runs` | Runs that exceeded timeout |
| `success_rate_pct` | % SUCCEEDED |
| `failure_rate_pct` | % FAILED |
| `avg_run_duration_seconds` | Mean wall-clock duration |
| `p95_run_duration_seconds` | 95th-percentile duration |
| `max_run_duration_seconds` | Slowest single run |
| `avg_queue_duration_seconds` | Mean time waiting for compute |

**Example — success rate by job last 30 days:**
```sql
SELECT job_name,
       SUM(total_runs)       AS runs,
       SUM(succeeded_runs)   AS succeeded,
       SUM(failed_runs)      AS failed,
       AVG(success_rate_pct) AS success_pct
FROM vireox_infra.semantic.vw_job_run_stats
WHERE run_date >= CURRENT_DATE - INTERVAL 30 DAYS
GROUP BY job_name
ORDER BY success_pct ASC
LIMIT 20
```

**Example — p95 duration trend by week:**
```sql
SELECT DATE_TRUNC('week', run_date) AS week,
       job_name,
       PERCENTILE(avg_run_duration_seconds, 0.95) AS p95_seconds
FROM vireox_infra.semantic.vw_job_run_stats
WHERE run_date >= CURRENT_DATE - INTERVAL 90 DAYS
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC
```

**When NOT to use this view:** For questions about individual run detail rows (specific run IDs, exact termination codes per run, task-level breakdown), use `system.lakeflow.job_run_timeline` directly with the standard pattern in the `jobs` skill.

---

## vw_query_perf — SQL Query Performance

**Location:** `vireox_infra.semantic.vw_query_perf`
**Source:** `system.query.history` (compute struct pre-flattened; success/cache flags pre-computed)

**Always filter by `query_date` or `start_time` within the 180-day window.**

**Fields (GROUP BY / WHERE)**

| Field | Description |
|---|---|
| `executed_by` | User or service principal |
| `execution_status` | FINISHED, FAILED, CANCELED — **never use** `status` |
| `statement_type` | SELECT, INSERT, CREATE, etc. |
| `compute_type` | WAREHOUSE or SERVERLESS_COMPUTE |
| `warehouse_id` | Warehouse / cluster ID |
| `query_date` | Calendar date (use for daily GROUP BY) |
| `start_time` | Query start timestamp |
| `from_result_cache` | Boolean — true if result cache served it |
| `client_application` | Client application that issued the query (e.g. Databricks SQL, dbt, JDBC, Tableau) |

**Measures (SELECT)**

| Measure | Description |
|---|---|
| `total_queries` | COUNT of all queries |
| `failed_queries` | COUNT of FAILED queries |
| `cancelled_queries` | COUNT of CANCELED queries |
| `success_rate_pct` | % FINISHED |
| `cache_hit_rate_pct` | % served from result cache |
| `avg_duration_ms` | Mean end-to-end query time (ms) |
| `p95_duration_ms` | 95th-percentile duration (ms) |
| `p99_duration_ms` | 99th-percentile duration (ms) |
| `avg_execution_ms` | Mean pure execution time (excl. compile + queue) |
| `avg_compilation_ms` | Mean query planning time (ms) |
| `avg_queue_wait_ms` | Mean time waiting in warehouse queue |
| `total_bytes_read` | Total bytes scanned |
| `avg_bytes_read` | Average bytes scanned per query |

**Example — p95 latency by user last 7 days:**
```sql
SELECT executed_by,
       SUM(total_queries)    AS queries,
       AVG(avg_duration_ms)  AS avg_ms,
       MAX(p95_duration_ms)  AS p95_ms
FROM vireox_infra.semantic.vw_query_perf
WHERE query_date >= CURRENT_DATE - INTERVAL 7 DAYS
GROUP BY executed_by
ORDER BY p95_ms DESC
LIMIT 20
```

**Example — top usage by client application last 30 days:**
```sql
SELECT client_application,
       SUM(total_queries)      AS queries,
       AVG(avg_duration_ms)    AS avg_ms,
       AVG(cache_hit_rate_pct) AS cache_hit_pct
FROM vireox_infra.semantic.vw_query_perf
WHERE query_date >= CURRENT_DATE - INTERVAL 30 DAYS
  AND client_application IS NOT NULL
GROUP BY client_application
ORDER BY queries DESC
LIMIT 20
```

**Example — daily cache hit rate trend:**
```sql
SELECT query_date,
       SUM(total_queries)        AS queries,
       AVG(cache_hit_rate_pct)   AS cache_hit_pct
FROM vireox_infra.semantic.vw_query_perf
WHERE query_date >= CURRENT_DATE - INTERVAL 30 DAYS
GROUP BY query_date
ORDER BY query_date
```
