-- ============================================================
-- Step 1 — Base view: flattens the compute struct and
--   pre-computes success/failure/cache boolean flags.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic._base_query_perf AS
SELECT
  workspace_id,
  statement_id,
  executed_by,
  execution_status,
  statement_type,
  compute.type       AS compute_type,
  compute.cluster_id AS warehouse_id,
  start_time,
  DATE(start_time)   AS query_date,
  total_duration_ms,
  execution_duration_ms,
  compilation_duration_ms,
  waiting_for_compute_duration_ms,
  waiting_at_capacity_duration_ms,
  read_bytes,
  read_rows,
  produced_rows,
  written_bytes,
  from_result_cache,
  CASE WHEN execution_status = 'FINISHED' THEN 1 ELSE 0 END AS is_success,
  CASE WHEN execution_status = 'FAILED'   THEN 1 ELSE 0 END AS is_failure,
  CASE WHEN execution_status = 'CANCELED' THEN 1 ELSE 0 END AS is_cancelled,
  CASE WHEN from_result_cache = true      THEN 1 ELSE 0 END AS is_cached
FROM system.query.history;

-- ============================================================
-- Step 2 — Metric view: semantic layer on top of the base view.
--   Always filter query_date or start_time to the 180-day
--   window when querying.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic.vw_query_perf
WITH METRICS LANGUAGE YAML AS$$
version: 1.1
comment: "Query execution performance on SQL warehouses and serverless compute. compute struct is pre-flattened; success/cache flags are pre-computed. Always filter query_date or start_time to the 180-day window. Use execution_status, never status."
source: vireox_infra.semantic._base_query_perf

fields:
  - name: workspace_id
    expr: workspace_id

  - name: executed_by
    expr: executed_by
    comment: "User email or service principal that ran the query"
    synonyms: [user, run_by, submitter]
    display_name: "User"

  - name: execution_status
    expr: execution_status
    comment: "FINISHED, FAILED, or CANCELED — never use the column name 'status'"
    synonyms: [status, result, outcome]

  - name: statement_type
    expr: statement_type
    comment: "SELECT, INSERT, CREATE, MERGE, etc."
    synonyms: [query_type, type]

  - name: compute_type
    expr: compute_type
    comment: "WAREHOUSE or SERVERLESS_COMPUTE"
    synonyms: [compute, engine]

  - name: warehouse_id
    expr: warehouse_id
    comment: "SQL warehouse or serverless cluster ID"

  - name: query_date
    expr: query_date
    comment: "Calendar date the query ran — use for daily GROUP BY"
    synonyms: [date, day]

  - name: start_time
    expr: start_time
    comment: "Query start timestamp — use for sub-day filtering"

  - name: from_result_cache
    expr: from_result_cache
    comment: "True if the result was served from the result cache"

measures:
  - name: total_queries
    expr: COUNT(*)
    comment: "Total number of queries executed"
    synonyms: [query_count, queries, executions]

  - name: failed_queries
    expr: SUM(is_failure)
    comment: "Number of queries that failed"
    synonyms: [failures, errors, error_count]

  - name: cancelled_queries
    expr: SUM(is_cancelled)
    comment: "Number of queries that were cancelled"

  - name: success_rate_pct
    expr: 100.0 * SUM(is_success) / NULLIF(COUNT(*), 0)
    comment: "Percentage of queries that finished successfully (0-100 scale)"
    synonyms: [success_rate, pass_rate, completion_rate]
    format:
      type: number
      decimal_places:
        type: exact
        places: 1

  - name: cache_hit_rate_pct
    expr: 100.0 * SUM(is_cached) / NULLIF(COUNT(*), 0)
    comment: "Percentage of queries served from the result cache (0-100 scale)"
    synonyms: [cache_rate, cache_hit, cache_efficiency]
    format:
      type: number
      decimal_places:
        type: exact
        places: 1

  - name: avg_duration_ms
    expr: AVG(total_duration_ms)
    comment: "Average end-to-end query duration in milliseconds"
    synonyms: [avg_duration, mean_latency, average_query_time]

  - name: p95_duration_ms
    expr: PERCENTILE(total_duration_ms, 0.95)
    comment: "95th-percentile query duration in milliseconds"
    synonyms: [p95, p95_latency, p95_duration]

  - name: p99_duration_ms
    expr: PERCENTILE(total_duration_ms, 0.99)
    comment: "99th-percentile query duration in milliseconds"
    synonyms: [p99, p99_latency]

  - name: avg_execution_ms
    expr: AVG(execution_duration_ms)
    comment: "Average pure execution time (excludes compilation and queue wait)"

  - name: avg_compilation_ms
    expr: AVG(compilation_duration_ms)
    comment: "Average query planning and compilation time in milliseconds"

  - name: avg_queue_wait_ms
    expr: AVG(waiting_at_capacity_duration_ms)
    comment: "Average time queries waited in the warehouse queue"
    synonyms: [avg_wait, queue_latency, queue_time]

  - name: total_bytes_read
    expr: SUM(read_bytes)
    comment: "Total bytes scanned across all queries"
    synonyms: [bytes_scanned, data_scanned]

  - name: avg_bytes_read
    expr: AVG(read_bytes)
    comment: "Average bytes scanned per query"
$$
