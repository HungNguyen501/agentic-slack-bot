"""Supabase / PostgreSQL connector — schedule CRUD."""
import os

import psycopg
from psycopg.rows import dict_row

_DB_URL = os.environ["SUPABASE_DB_URL"]


def _connect() -> psycopg.Connection:
    """Open a psycopg3 connection to Supabase that returns rows as dicts."""
    return psycopg.connect(_DB_URL, row_factory=dict_row)


def get_schedules(bot_id: str | None = None) -> list[dict]:
    """Fetch schedules from Supabase ordered by creation time.

    Args:
        bot_id: If provided, returns only schedules for that bot. Otherwise returns all schedules.

    Returns:
        List of schedule dicts with string-serialized id, cron, channel, question, and bot_id fields.
    """
    with _connect() as conn, conn.cursor() as cur:
        if bot_id is not None:
            cur.execute(
                "SELECT id, cron, channel, question, bot_id FROM schedules "
                "WHERE bot_id = %s OR bot_id IS NULL ORDER BY created_at",
                (bot_id,),
            )
        else:
            cur.execute("SELECT id, cron, channel, question, bot_id FROM schedules ORDER BY created_at")
        return [_serialize(r) for r in cur.fetchall()]


def add_schedule(cron: str, channel: str, question: str, bot_id: str | None = None) -> dict:
    """Insert a new schedule row and return the created record.

    Args:
        cron: Cron expression in UTC, e.g. "0 9 * * 1-5".
        channel: Slack channel ID to post the scheduled question into.
        question: The question text the bot will ask on each trigger.
        bot_id: Bot that owns this schedule; None means it runs with the default bot.

    Returns:
        The newly created schedule as a dict with id, cron, channel, question, bot_id, and created_at.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schedules (cron, channel, question, bot_id) VALUES (%s, %s, %s, %s) "
            "RETURNING id, cron, channel, question, bot_id, created_at",
            (cron, channel, question, bot_id),
        )
        return _serialize(cur.fetchone())


def update_schedule(
    id: str,
    cron: str | None = None,
    channel: str | None = None,
    question: str | None = None,
) -> dict | None:
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
    sql = f"UPDATE schedules SET {', '.join(fields)} WHERE id = %s RETURNING id, cron, channel, question, bot_id, updated_at"
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


def get_access_request_category(bot_id: str, request_type: str) -> dict | None:
    """Fetch the access-control row gating a data access request type for a bot.

    Args:
        bot_id: Bot identifier the request was made through.
        request_type: The requested access category, e.g. "table_row_filter_access".

    Returns:
        Dict with id, bot_id, request_type, channel_ids, and reviewers (the latter two as
        real lists, not serialized), or None if no category is configured for this
        bot_id/request_type pair. Not run through _serialize since channel_ids/reviewers
        are consumed as lists (containment checks), not passed back to the LLM.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, bot_id, request_type, channel_ids, reviewers FROM access_request_categories "
            "WHERE bot_id = %s AND request_type = %s",
            (bot_id, request_type),
        )
        return cur.fetchone()


def channel_authorized(category: dict | None, channel: str) -> bool:
    """True if `category` (from get_access_request_category) permits requests in `channel`.

    Shared by the three places that must independently re-check this — asking for the form
    (worker/agent.py), persisting a submission (worker/tasks.py), and approving (worker/review.py)
    — since a category can be revoked at any point between those steps.
    """
    return bool(category) and channel in category["channel_ids"]


def add_access_request(
    bot_id: str,
    request_type: str,
    channel: str,
    thread_ts: str,
    requester_id: str | None,
    reviewers: list[str],
    ticket_id: str,
    user_email: str,
    principal_type: str,
    display_name: str,
    principal: str,
    filter_column: str,
    allowed_value: str,
    scope_column: str,
    scope_value: str,
    groups: list[str],
    tags: list[str],
) -> dict:
    """Insert a newly submitted access request row with status='pending'.

    Returns:
        The created row (raw, not _serialize'd — reviewers/groups/tags are consumed as
        lists by worker/review.py, not passed back to the LLM). Includes the generated `id`,
        shown to the requester so reviewers can reference it unambiguously.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO access_requests ("
            "bot_id, request_type, channel, thread_ts, requester_id, reviewers, ticket_id, user_email, "
            "principal_type, display_name, principal, filter_column, allowed_value, scope_column, scope_value, "
            "groups, tags"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (
                bot_id,
                request_type,
                channel,
                thread_ts,
                requester_id,
                reviewers,
                ticket_id,
                user_email,
                principal_type,
                display_name,
                principal,
                filter_column,
                allowed_value,
                scope_column,
                scope_value,
                groups,
                tags,
            ),
        )
        return cur.fetchone()


def get_access_request(bot_id: str, request_id: str, thread_ts: str) -> dict | None:
    """Fetch a specific access request by id, scoped to the thread it's being reviewed in.

    Scoping by thread_ts (in addition to id) prevents a reply in one thread from acting on a
    request that was submitted in a different thread.

    Args:
        bot_id: Bot the request was made through.
        request_id: The access_requests.id a reviewer referenced (e.g. "approve <request_id>").
        thread_ts: Slack thread timestamp the reply was posted in.

    Returns:
        The matching row (raw dict, lists as real lists), or None if no such request exists
        in this thread (including when request_id isn't a valid UUID).
    """
    with _connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT * FROM access_requests WHERE bot_id = %s AND id = %s AND thread_ts = %s",
                (bot_id, request_id, thread_ts),
            )
        except psycopg.errors.InvalidTextRepresentation:
            conn.rollback()
            return None
        return cur.fetchone()


def update_access_request_status(
    id: str,
    status: str,
    *,
    pr_url: str | None = None,
    service_principal_id: str | None = None,
    principal: str | None = None,
    display_name: str | None = None,
) -> None:
    """Update an access request's status and, on approval, the resolved principal fields.

    Args:
        id: UUID of the access request row.
        status: New status — "approved" or "rejected".
        pr_url: The opened pull request's URL, set on approval.
        service_principal_id: The Databricks SCIM id of the (found-or-created) service
            principal, set on approval for the "Service principals" path only.
        principal: The resolved principal identifier (applicationId or user email).
        display_name: The resolved display name.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE access_requests SET status = %s, pr_url = COALESCE(%s, pr_url), "
            "service_principal_id = COALESCE(%s, service_principal_id), "
            "principal = COALESCE(%s, principal), display_name = COALESCE(%s, display_name) "
            "WHERE id = %s",
            (status, pr_url, service_principal_id, principal, display_name, id),
        )


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
