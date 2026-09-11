"""Databricks Jobs API client."""
import traceback

import httpx

from ._client import HOST, TOKEN


def get_job_run(run_id: str | int) -> str:
    """Fetch error details for a specific Databricks job run via the Jobs REST API.

    Args:
        run_id: The job run ID (from job_run_timeline.run_id).

    Returns:
        Slack-formatted string with the run state, error message, and per-task errors,
        or a plain-text error string if the request fails.
    """
    url = f"{HOST}/api/2.1/jobs/runs/get"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    params = {"run_id": str(run_id), "include_resolved_values": "false"}

    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"Databricks HTTP error {exc.response.status_code}: {exc.response.text[:500]}"
    except Exception:
        return f"Error fetching job run:\n{traceback.format_exc()}"

    state = data.get("state", {})
    result_state = state.get("result_state", "UNKNOWN")
    state_message = state.get("state_message") or "(no error message)"
    job_id = data.get("job_id", "unknown")
    run_name = data.get("run_name") or data.get("run_id", run_id)

    lines = [
        f"*Run {run_id}* — Job ID: `{job_id}` | Name: {run_name}",
        f"*State:* {result_state}",
        f"*Error:* {state_message}",
    ]

    tasks = data.get("tasks", [])
    failed_tasks = [t for t in tasks if t.get("state", {}).get("result_state") not in ("SUCCESS", "SUCCEEDED", None)]
    if failed_tasks:
        lines.append(f"\n*Failed tasks ({len(failed_tasks)}):*")
        for t in failed_tasks:
            task_state = t.get("state", {})
            task_msg = task_state.get("state_message") or "(no message)"
            lines.append(f"  • `{t.get('task_key', '?')}` — {task_state.get('result_state', '?')}: {task_msg}")

    return "\n".join(lines)
