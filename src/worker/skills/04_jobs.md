---
name: jobs
always: false
description: Use when the question is about job definitions, task structures, task dependencies, job schedules, trigger types, job run history, run success/failure rates, run durations, or pipeline workflows.
---

## Skill: Jobs & Pipelines

**system.lakeflow.jobs** *(SCD2 — no time restriction)*
- Columns: `account_id`, `workspace_id`, `job_id`, `name`, `description`, `creator_id`, `creator_user_name`, `run_as`, `run_as_user_name`, `trigger_type`, `trigger` (struct), `paused`, `timeout_seconds`, `tags`, `create_time`, `change_time`, `delete_time`
- `creator_user_name` / `run_as_user_name` — human-readable email; prefer these over ID fields when filtering by user
- `change_time` — use to identify the latest SCD2 record per `job_id`
- `delete_time IS NULL` — active jobs only; non-NULL means deleted

**Rules — always apply when querying this table:**
- Filter `WHERE delete_time IS NULL` to exclude deleted jobs (unless the user explicitly asks about deletion history)
- Deduplicate with `QUALIFY ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) = 1`
- Never expose `job_id` alone in results — always join to show `name`

**system.lakeflow.job_tasks** *(SCD2 — no time restriction)*
- Columns: `account_id`, `workspace_id`, `job_id`, `task_key`, `depends_on_keys` (ARRAY of upstream task keys), `timeout_seconds`, `health_rules`, `change_time`, `delete_time`
- `depends_on_keys` is the correct column for task dependencies — not `depends_on`
- **Does NOT have:** `task_type`, `notebook_path`, `existing_cluster_id`, `new_cluster`, `libraries` — task config details are not stored in this system table
- Apply the same SCD2 dedup (`QUALIFY` + `delete_time IS NULL`) rules as `lakeflow.jobs`

**system.lakeflow.job_run_timeline** *(180-day filter on `period_start_time`)*
- Columns: `account_id`, `workspace_id`, `job_id`, `run_id`, `run_name`, `trigger_type`, `run_type` (JOB_RUN / SUBMIT_RUN / WORKFLOW_RUN), `result_state`, `termination_code`, `termination_type`, `period_start_time`, `period_end_time`, `run_duration_seconds`, `execution_duration_seconds`, `setup_duration_seconds`, `queue_duration_seconds`, `cleanup_duration_seconds`, `compute_ids`, `compute` (array of structs), `job_parameters`
- `result_state` and `termination_code` are only populated in the **final hourly slice row** for long-running jobs — always filter `result_state IS NOT NULL` to get run outcomes
- `result_state` values: SUCCEEDED, FAILED, SKIPPED, CANCELLED, TIMED_OUT, ERROR, BLOCKED
- Use `run_duration_seconds` directly — do not compute duration from timestamps

Standard pattern — always use when querying job runs:
```sql
WITH latest_jobs AS (
    SELECT job_id, name, creator_user_name, run_as_user_name
    FROM system.lakeflow.jobs
    WHERE delete_time IS NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) = 1
)
SELECT j.name AS job_name, j.creator_user_name,
       r.run_id, r.result_state, r.termination_code, r.trigger_type,
       r.period_start_time, r.period_end_time, r.run_duration_seconds
FROM system.lakeflow.job_run_timeline r
JOIN latest_jobs j ON r.job_id = j.job_id
WHERE r.period_start_time >= CURRENT_DATE - INTERVAL 180 DAYS
  AND r.result_state IS NOT NULL
```

**system.lakeflow.job_task_run_timeline** *(180-day filter on `period_start_time`)*
- Columns: `account_id`, `workspace_id`, `job_id`, `run_id` (task-level run ID), `job_run_id` (parent job run ID), `parent_run_id`, `task_key`, `result_state`, `termination_code`, `termination_type`, `period_start_time`, `period_end_time`, `execution_duration_seconds`, `setup_duration_seconds`, `cleanup_duration_seconds`, `compute_ids`, `compute` (array of structs), `task_parameters`
- `run_id` here is the **task-level** run ID — use `job_run_id` to join with `job_run_timeline`
- `result_state` is only populated in the **final slice row** — always filter `result_state IS NOT NULL`
- Use `execution_duration_seconds` directly for task duration

Standard pattern — always use when querying task runs:
```sql
WITH latest_jobs AS (
    SELECT job_id, name
    FROM system.lakeflow.jobs
    WHERE delete_time IS NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) = 1
)
SELECT j.name AS job_name,
       tr.task_key, tr.job_run_id, tr.result_state, tr.termination_code,
       tr.period_start_time, tr.period_end_time, tr.execution_duration_seconds
FROM system.lakeflow.job_task_run_timeline tr
JOIN latest_jobs j ON tr.job_id = j.job_id
WHERE tr.period_start_time >= CURRENT_DATE - INTERVAL 180 DAYS
  AND tr.result_state IS NOT NULL
```

**Job & task run links:**
URL: `https://adb-1072468836148393.13.azuredatabricks.net/jobs/<job_id>/runs/<run_id>`
- Job run: `run_id` = `job_run_timeline.run_id`
- Task run: `run_id` = `job_task_run_timeline.run_id` (task-level ID)

Always format as a Slack embedded link: `<URL|Job: <job_name> — Run <run_id>>`
