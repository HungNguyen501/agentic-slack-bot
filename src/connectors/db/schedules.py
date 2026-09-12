"""Supabase schedules table — schedule CRUD."""
from dataclasses import dataclass
from typing import Any

from connectors.db.connection import connect


@dataclass
class Schedule:
    id: str
    cron: str
    channel: str
    question: str
    bot_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _row_to_schedule(row: Any) -> Schedule:
    return Schedule(
        id=str(row["id"]),
        cron=row["cron"],
        channel=row["channel"],
        question=row["question"],
        bot_id=str(row["bot_id"]) if row.get("bot_id") else None,
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


def get_schedules(bot_id: str | None = None) -> list[Schedule]:
    """Fetch schedules from Supabase ordered by creation time.

    Args:
        bot_id: If provided, returns only schedules for that bot. Otherwise returns all schedules.

    Returns:
        List of Schedule records.
    """
    with connect() as conn, conn.cursor() as cur:
        if bot_id is not None:
            cur.execute(
                "SELECT id, cron, channel, question, bot_id FROM schedules "
                "WHERE bot_id = %s OR bot_id IS NULL ORDER BY created_at",
                (bot_id,),
            )
        else:
            cur.execute("SELECT id, cron, channel, question, bot_id FROM schedules ORDER BY created_at")
        return [_row_to_schedule(r) for r in cur.fetchall()]


def add_schedule(cron: str, channel: str, question: str, bot_id: str | None = None) -> Schedule:
    """Insert a new schedule row and return the created record.

    Args:
        cron: Cron expression in UTC, e.g. "0 9 * * 1-5".
        channel: Slack channel ID to post the scheduled question into.
        question: The question text the bot will ask on each trigger.
        bot_id: Bot that owns this schedule; None means it runs with the default bot.

    Returns:
        The newly created Schedule record.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schedules (cron, channel, question, bot_id) VALUES (%s, %s, %s, %s) "
            "RETURNING id, cron, channel, question, bot_id, created_at",
            (cron, channel, question, bot_id),
        )
        return _row_to_schedule(cur.fetchone())


def update_schedule(
    id: str,
    cron: str | None = None,
    channel: str | None = None,
    question: str | None = None,
) -> Schedule | None:
    """Update one or more fields of an existing schedule row.

    Args:
        id: UUID of the schedule to update.
        cron: New cron expression; omit to leave unchanged.
        channel: New Slack channel ID; omit to leave unchanged.
        question: New question text; omit to leave unchanged.

    Returns:
        The updated Schedule record on success, or None if no fields were provided or the id
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
    sql = f"UPDATE schedules SET {', '.join(fields)} WHERE id = %s RETURNING id, cron, channel, question, bot_id, updated_at"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return _row_to_schedule(row) if row else None


def remove_schedule(id: str) -> bool:
    """Delete a schedule row by its UUID.

    Args:
        id: UUID of the schedule to remove.

    Returns:
        True if a row was deleted, False if no row matched the given id.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schedules WHERE id = %s", (id,))
        return cur.rowcount > 0
