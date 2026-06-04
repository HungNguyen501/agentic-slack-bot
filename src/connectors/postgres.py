"""Supabase / PostgreSQL connector — schedule CRUD."""
import os

import psycopg
from psycopg.rows import dict_row

_DB_URL = os.environ["SUPABASE_DB_URL"]


def _connect() -> psycopg.Connection:
    """Open a psycopg3 connection to Supabase that returns rows as dicts."""
    return psycopg.connect(_DB_URL, row_factory=dict_row)


def get_schedules() -> list[dict]:
    """Fetch all schedules from Supabase ordered by creation time.

    Returns:
        List of schedule dicts with string-serialized id, cron, channel, and question fields.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, cron, channel, question FROM schedules ORDER BY created_at")
        return [_serialize(r) for r in cur.fetchall()]


def add_schedule(cron: str, channel: str, question: str) -> dict:
    """Insert a new schedule row and return the created record.

    Args:
        cron: Cron expression in UTC, e.g. "0 9 * * 1-5".
        channel: Slack channel ID to post the scheduled question into.
        question: The question text the bot will ask on each trigger.

    Returns:
        The newly created schedule as a dict with id, cron, channel, question, and created_at.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schedules (cron, channel, question) VALUES (%s, %s, %s) "
            "RETURNING id, cron, channel, question, created_at",
            (cron, channel, question),
        )
        return _serialize(cur.fetchone())


def update_schedule(id: str, cron: str | None = None, channel: str | None = None, question: str | None = None) -> dict | None:
    """Update one or more fields of an existing schedule row.

    Args:
        id: UUID of the schedule to update.
        cron: New cron expression; omit to leave unchanged.
        channel: New Slack channel ID; omit to leave unchanged.
        question: New question text; omit to leave unchanged.

    Returns:
        The updated schedule dict on success, or None if no fields were provided or the id
        was not found.
    """
    fields, params = [], []

    if cron is not None:
        fields.append("cron = %s")
        params.append(cron)

    if channel is not None:
        fields.append("channel = %s")
        params.append(channel)

    if question is not None:
        fields.append("question = %s")
        params.append(question)

    if not fields:
        return None

    params.append(id)
    sql = f"UPDATE schedules SET {', '.join(fields)} WHERE id = %s RETURNING id, cron, channel, question, updated_at"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return _serialize(row) if row else None


def remove_schedule(id: str) -> bool:
    """Delete a schedule row by its UUID.

    Args:
        id: UUID of the schedule to remove.

    Returns:
        True if a row was deleted, False if no row matched the given id.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schedules WHERE id = %s", (id,))
        return cur.rowcount > 0


def _serialize(row: dict | None) -> dict:
    """Convert a psycopg row dict to plain strings for JSON and LLM compatibility.

    Args:
        row: A dict-row from psycopg that may contain UUID or datetime values.

    Returns:
        A new dict with every non-None value converted to str; None values are preserved.
    """
    if row is None:
        return {}
    return {k: str(v) if v is not None else None for k, v in row.items()}
