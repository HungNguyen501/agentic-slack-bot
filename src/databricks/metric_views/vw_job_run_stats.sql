-- ============================================================
-- Step 1 — Base view: resolves SCD2 on lakeflow.jobs, joins to
--   job_run_timeline, and pre-computes outcome flag columns.
--   result_state IS NOT NULL filter is applied here.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic._base_job_run_stats AS
WITH latest_jobs AS (
  SELECT
    job_id,
    workspace_id,
    name              AS job_name,
    creator_user_name,
    run_as_user_name
  FROM system.lakeflow.jobs
  WHERE delete_time IS NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY workspace_id, job_id ORDER BY change_time DESC
  ) = 1
)
SELECT
  r.workspace_id,
  r.job_id,
  j.job_name,
  j.creator_user_name,
  j.run_as_user_name,
  r.run_id,
  r.trigger_type,
  r.run_type,
  r.result_state,
  r.termination_code,
  r.period_start_time,
  DATE(r.period_start_time)                                 AS run_date,
  r.run_duration_seconds,
  r.execution_duration_seconds,
  r.queue_duration_seconds,
  CASE WHEN r.result_state = 'SUCCEEDED' THEN 1 ELSE 0 END AS is_success,
  CASE WHEN r.result_state = 'FAILED'    THEN 1 ELSE 0 END AS is_failure,
  CASE WHEN r.result_state = 'CANCELLED' THEN 1 ELSE 0 END AS is_cancelled,
  CASE WHEN r.result_state = 'TIMED_OUT' THEN 1 ELSE 0 END AS is_timed_out
FROM system.lakeflow.job_run_timeline r
JOIN latest_jobs j
  ON r.workspace_id = j.workspace_id AND r.job_id = j.job_id
WHERE r.result_state IS NOT NULL;

-- ============================================================
-- Step 2 — Metric view: semantic layer on top of the base view.
--   Always filter run_date or period_start_time to the 180-day
--   window when querying.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic.vw_job_run_stats
WITH METRICS LANGUAGE YAML AS$$
version: 1.1
comment: "Job run outcomes and durations with job names resolved. SCD2 dedup and result_state filter are pre-applied. Always filter run_date or period_start_time to the 180-day window."
source: vireox_infra.semantic._base_job_run_stats

fields:
  - name: workspace_id
    expr: workspace_id

  - name: job_id
    expr: job_id
    comment: "Lakeflow job ID — use for constructing run URLs"

  - name: job_name
    expr: job_name
    comment: "User-assigned job name — already resolved, no join needed"
    synonyms: [job, name]
    display_name: "Job Name"

  - name: creator_user_name
    expr: creator_user_name
    comment: "Email of the user who created the job"
    synonyms: [creator, created_by, author]

  - name: run_as_user_name
    expr: run_as_user_name
    comment: "Identity the job runs as"
    synonyms: [run_as, owner, executing_user]

  - name: run_id
    expr: run_id
    comment: "Unique run identifier — use in job run URL construction"

  - name: trigger_type
    expr: trigger_type
    comment: "SCHEDULED, MANUAL, FILE_ARRIVAL, CONTINUOUS, etc."
    synonyms: [trigger, schedule_type]

  - name: run_type
    expr: run_type
    comment: "JOB_RUN, SUBMIT_RUN, or WORKFLOW_RUN"

  - name: result_state
    expr: result_state
    comment: "SUCCEEDED, FAILED, CANCELLED, TIMED_OUT, SKIPPED, ERROR, BLOCKED"
    synonyms: [status, outcome, state]

  - name: termination_code
    expr: termination_code
    comment: "Detailed termination reason code"

  - name: run_date
    expr: run_date
    comment: "Calendar date of the run — use for daily GROUP BY"
    synonyms: [date, day]

  - name: period_start_time
    expr: period_start_time
    comment: "Run start timestamp"

measures:
  - name: total_runs
    expr: COUNT(*)
    comment: "Total number of completed job runs"
    synonyms: [runs, run_count, executions]

  - name: succeeded_runs
    expr: SUM(is_success)
    comment: "Number of runs with result_state = SUCCEEDED"
    synonyms: [successful_runs, passes]

  - name: failed_runs
    expr: SUM(is_failure)
    comment: "Number of runs with result_state = FAILED"
    synonyms: [failures, errors]

  - name: cancelled_runs
    expr: SUM(is_cancelled)
    comment: "Number of runs with result_state = CANCELLED"

  - name: timed_out_runs
    expr: SUM(is_timed_out)
    comment: "Number of runs that exceeded their timeout"

  - name: success_rate_pct
    expr: 100.0 * SUM(is_success) / NULLIF(COUNT(*), 0)
    comment: "Percentage of runs that succeeded (0-100 scale)"
    synonyms: [success_rate, pass_rate, reliability]
    format:
      type: number
      decimal_places:
        type: exact
        places: 1

  - name: failure_rate_pct
    expr: 100.0 * SUM(is_failure) / NULLIF(COUNT(*), 0)
    comment: "Percentage of runs that failed (0-100 scale)"
    synonyms: [failure_rate, error_rate]
    format:
      type: number
      decimal_places:
        type: exact
        places: 1

  - name: avg_run_duration_seconds
    expr: AVG(run_duration_seconds)
    comment: "Average total job run wall-clock duration in seconds"
    synonyms: [avg_duration, mean_duration, average_runtime]

  - name: p95_run_duration_seconds
    expr: PERCENTILE(run_duration_seconds, 0.95)
    comment: "95th-percentile job run duration in seconds"
    synonyms: [p95_duration, p95, tail_latency]

  - name: max_run_duration_seconds
    expr: MAX(run_duration_seconds)
    comment: "Slowest single run in the queried set"
    synonyms: [max_duration, slowest_run]

  - name: avg_queue_duration_seconds
    expr: AVG(queue_duration_seconds)
    comment: "Average time runs spent waiting for compute to become available"
    synonyms: [avg_queue_time, wait_time]
$$
