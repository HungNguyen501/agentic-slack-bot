"""Databricks Statement API client."""
import logging
import os
import traceback

import httpx

log = logging.getLogger("connectors.databricks")

_HOST = os.environ["DATABRICKS_HOST"].rstrip("/")
_WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
_TOKEN = os.environ["DATABRICKS_ACCESS_TOKEN"]

_MAX_ROWS = 200


def run_query(sql: str) -> str:
    """Execute a read-only SQL statement against Databricks and return a Slack-formatted result table.

    Args:
        sql: A SQL statement to run; must start with SELECT or WITH.

    Returns:
        Query results as a fixed-width Slack code block, or a plain-text error string if
        the query violates the read-only guard, fails on Databricks, or exceeds the 50s timeout.
    """
    stripped = sql.strip()
    upper = stripped.upper()

    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "Error: only SELECT queries are permitted."

    url = f"{_HOST}/api/2.0/sql/statements"
    headers = {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "warehouse_id": _WAREHOUSE_ID,
        "statement": stripped,
        "wait_timeout": "50s",
        "on_wait_timeout": "CANCEL",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"Databricks HTTP error {exc.response.status_code}: {exc.response.text[:500]}"
    except Exception:
        return f"Error querying Databricks:\n{traceback.format_exc()}"

    state = data.get("status", {}).get("state", "")
    if state == "FAILED":
        msg = data.get("status", {}).get("error", {}).get("message", "unknown error")
        return f"Query failed: {msg}"
    if state == "CANCELLED":
        return "Query was cancelled (exceeded 50s timeout)."

    manifest = data.get("manifest", {})
    columns = [c["name"] for c in manifest.get("schema", {}).get("columns", [])]
    rows = data.get("result", {}).get("data_array", [])

    if not rows:
        return "Query returned no results."

    truncated = len(rows) > _MAX_ROWS
    display_rows = rows[:_MAX_ROWS]

    all_rows = [columns] + [
        ["NULL" if cell is None else str(cell) for cell in row]
        for row in display_rows
    ]
    col_widths = [max(len(r[i]) for r in all_rows) for i in range(len(columns))]
    divider = "  ".join("-" * w for w in col_widths)
    lines = []
    for i, row in enumerate(all_rows):
        lines.append("  ".join(cell.ljust(col_widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            lines.append(divider)

    note = f"(showing first {_MAX_ROWS} of {len(rows)} rows)" if truncated else f"{len(rows)} row(s)"
    return f"```\n{'\n'.join(lines)}\n```\n_{note}_"
