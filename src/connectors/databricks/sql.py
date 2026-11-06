"""Databricks Statement API client."""
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from .client import HOST, WAREHOUSE_ID, TOKEN

_MAX_ROWS = 200


class DatabricksQueryError(Exception):
    """A statement didn't produce a complete result; the message says why and is fit to show the user."""


@dataclass(frozen=True)
class RowSet:
    """A complete statement result: column names and one dict per row.

    Values are what the Statement API's JSON_ARRAY format returns: strings, or None for SQL NULL.
    """

    columns: list[str]
    rows: list[dict[str, str | None]]


def _require_select(sql: str) -> str:
    """The statement trimmed of whitespace and a trailing semicolon, if it is a SELECT or WITH."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped.upper().startswith(("SELECT", "WITH")):
        raise DatabricksQueryError("only SELECT queries are permitted.")
    return stripped


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


@contextmanager
def _translate_http_errors() -> Iterator[None]:
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise DatabricksQueryError(f"Databricks HTTP error {exc.response.status_code}: {exc.response.text[:500]}") from exc
    except httpx.HTTPError as exc:
        raise DatabricksQueryError(f"Could not reach Databricks: {exc}") from exc


def _execute_statement(statement: str, row_limit: int | None = None) -> dict:
    """Run a statement on the warehouse (50 s wait, then cancel) and return the Statement API's JSON once it succeeded.

    Raises:
        DatabricksQueryError: the API can't be reached or answers with an error, or the statement failed or was cancelled.
    """
    payload = {
        "warehouse_id": WAREHOUSE_ID,
        "statement": statement,
        "wait_timeout": "50s",
        "on_wait_timeout": "CANCEL",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }
    if row_limit is not None:
        payload["row_limit"] = row_limit
    with _translate_http_errors():
        resp = httpx.post(f"{HOST}/api/2.0/sql/statements", headers=_auth_headers(), json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()

    status = data.get("status", {})
    state = status.get("state", "")
    if state == "FAILED":
        raise DatabricksQueryError(f"Query failed: {status.get('error', {}).get('message', 'unknown error')}")
    if state in ("CANCELED", "CANCELLED"):
        raise DatabricksQueryError("Query was cancelled (exceeded 50s timeout).")
    if state != "SUCCEEDED":
        raise DatabricksQueryError(f"Query did not succeed (state: {state or 'unknown'}).")
    return data


def _read_all_rows(first_result: dict) -> list[list]:
    """The rows of a result, following `next_chunk_internal_link` through the remaining chunks."""
    rows: list[list] = []
    result = first_result
    with _translate_http_errors():
        while True:
            rows.extend(result.get("data_array") or [])
            next_chunk = result.get("next_chunk_internal_link")
            if not next_chunk:
                return rows
            chunk_resp = httpx.get(f"{HOST}{next_chunk}", headers=_auth_headers(), timeout=60.0)
            chunk_resp.raise_for_status()
            result = chunk_resp.json()


def _column_names(data: dict) -> list[str]:
    return [c["name"] for c in data.get("manifest", {}).get("schema", {}).get("columns", [])]


def run_query(sql: str) -> str:
    """Execute a read-only SQL statement against Databricks and return a Slack-formatted result table.

    Args:
        sql: A SQL statement to run; must start with SELECT or WITH.

    Returns:
        Query results as a fixed-width Slack code block, or a plain-text error string if
        the query violates the read-only guard, fails on Databricks, or exceeds the 50s timeout.
    """
    try:
        statement = _require_select(sql)
    except DatabricksQueryError as exc:
        return f"Error: {exc}"
    try:
        data = _execute_statement(statement)
    except DatabricksQueryError as exc:
        return str(exc)
    except Exception:
        return f"Error querying Databricks:\n{traceback.format_exc()}"

    columns = _column_names(data)
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


def fetch_rows(sql: str, *, max_rows: int) -> RowSet:
    """Run a read-only statement and return every row, unlike `run_query`, which caps and formats for Slack.

    Args:
        sql: A statement starting with SELECT or WITH; a trailing semicolon is dropped.
        max_rows: Most rows accepted. A bigger result is an error, never silently truncated.

    Raises:
        DatabricksQueryError: the statement isn't read-only, fails, is cancelled at the 50 s timeout, exceeds
            `max_rows`, or the API can't be reached.
    """
    data = _execute_statement(_require_select(sql), row_limit=max_rows + 1)
    rows = _read_all_rows(data.get("result", {}))
    if len(rows) > max_rows:
        raise DatabricksQueryError(f"Query returned more than {max_rows} rows.")
    columns = _column_names(data)
    return RowSet(columns=columns, rows=[dict(zip(columns, row, strict=True)) for row in rows])
