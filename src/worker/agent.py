"""Databricks assistant agent powered by OpenAI function calling."""
import json
import logging
import os
import traceback
from datetime import date, timedelta

import httpx
from openai import OpenAI
from redis import Redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker.agent")

GPT_MODEL = "gpt-5.4-2026-03-05"
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DATABRICKS_HOST = os.environ["DATABRICKS_HOST"].rstrip("/")
DATABRICKS_WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
DATABRICKS_ACCESS_TOKEN = os.environ["DATABRICKS_ACCESS_TOKEN"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

PROMPT_FILE_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")
HISTORY_TTL = 86400  # 24 hours
MAX_HISTORY_TURNS = 20  # keep last 20 user/assistant pairs

openai_client = OpenAI(api_key=OPENAI_API_KEY)
redis_client = Redis.from_url(REDIS_URL)


def _load_system_prompt() -> str:
    """Read from disk on every call so prompt edits take effect without a restart.

    Returns:
        System prompt text with leading/trailing whitespace stripped.
    """
    today = date.today()
    cutoff = today - timedelta(days=90)
    base = open(PROMPT_FILE_PATH).read().strip()
    return (
        f"Today's date: {today.isoformat()}\n"
        f"90-day window cutoff: {cutoff.isoformat()} — any date on or after this is within the allowed window.\n\n"
        f"{base}"
    )


def _load_history(thread_ts: str) -> list[dict]:
    """Return stored user/assistant message pairs for the thread, or an empty list.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.

    Returns:
        List of message dicts in OpenAI chat format (role + content).
    """
    raw = redis_client.get(f"chat_history:{thread_ts}")
    if raw:
        return json.loads(raw)
    return []


def _save_history(thread_ts: str, history: list[dict]) -> None:
    """Trim to the last MAX_HISTORY_TURNS pairs before persisting to Redis.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.
        history: Full list of user/assistant message dicts to persist.
    """
    max_messages = MAX_HISTORY_TURNS * 2

    if len(history) > max_messages:
        history = history[-max_messages:]

    redis_client.set(f"chat_history:{thread_ts}", json.dumps(history), ex=HISTORY_TTL)


def _get_agent_tool_calls() -> list[dict]:
    """ Define tool calls for Agent """
    return [
        {
            "type": "function",
            "function": {
                "name": "execute_query",
                "description": (
                    "Execute a SQL SELECT query against Databricks system tables. "
                    "Use this to answer questions about catalogs, schemas, tables, columns, "
                    "jobs, job run history, or data lineage."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A SQL SELECT statement targeting Databricks system tables.",
                        }
                    },
                    "required": ["sql"],
                },
            },
        }
    ]


def _run_databricks_query(sql: str) -> str:
    """Enforce SELECT-only guard, execute via Databricks Statement API, and return a Slack-formatted table.

    Args:
        sql: SQL statement to execute; must start with SELECT or WITH.

    Returns:
        Query results as a fixed-width Slack code block, or an error string on failure.
    """
    stripped = sql.strip()
    upper = stripped.upper()

    # Guard: only allow read-only queries
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "Error: only SELECT queries are permitted."

    url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
    headers = {
        "Authorization": f"Bearer {DATABRICKS_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "warehouse_id": DATABRICKS_WAREHOUSE_ID,
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
        return f"Error querying Databricks: {traceback.format_exc()}"

    state = data.get("status", {}).get("state", "")
    if state == "FAILED":
        msg = data.get("status", {}).get("error", {}).get("message", "unknown error")
        return f"Query failed: {msg}"
    if state == "CANCELLED":
        return "Query was cancelled (exceeded 30s timeout)."

    manifest = data.get("manifest", {})
    columns = [c["name"] for c in manifest.get("schema", {}).get("columns", [])]
    rows = data.get("result", {}).get("data_array", [])

    if not rows:
        return "Query returned no results."

    MAX_ROWS = 200
    truncated = len(rows) > MAX_ROWS
    display_rows = rows[:MAX_ROWS]

    # Build aligned fixed-width columns for a Slack code block
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
    table = "\n".join(lines)

    note = f"(showing first {MAX_ROWS} of {len(rows)} rows)" if truncated else f"{len(rows)} row(s)"
    return f"```\n{table}\n```\n_{note}_"


def run_agent(question: str, thread_ts: str) -> str:
    """Run the OpenAI agentic loop with per-thread conversation history.

    Args:
        question: User's question with the @mention prefix already stripped.
        thread_ts: Slack thread timestamp used to scope conversation history.

    Returns:
        Agent's final answer as a Slack-formatted string.
    """
    history = _load_history(thread_ts)
    log.info("Loaded %d history messages for thread %s", len(history), thread_ts)

    messages: list[dict] = [{"role": "system", "content": _load_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    for _ in range(10):  # safety cap on tool-call rounds
        response = openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=messages,
            tools=_get_agent_tool_calls(),
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            answer = msg.content or "I wasn't able to generate a response."
            # Persist only the user/assistant turn — tool calls are internal scaffolding
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
            _save_history(thread_ts, history)
            return answer

        # Append assistant message with tool calls
        messages.append(msg.model_dump())

        for tool_call in msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            sql = args.get("sql", "")
            log.info("Databricks query: %s", sql)
            result = _run_databricks_query(sql)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    return "Sorry, I hit a processing limit. Please try a more specific question."
