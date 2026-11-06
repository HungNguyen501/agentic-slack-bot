"""Supabase schedules table — schedule CRUD."""
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from psycopg.types.json import Jsonb

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
    kind: str = "question"
    status: str = "active"
    config: dict = field(default_factory=dict)


_COLUMNS = "id, cron, channel, question, bot_id, kind, status, config, created_at"


def _row_to_schedule(row: Any) -> Schedule:
    return Schedule(
        id=str(row["id"]),
        cron=row["cron"],
        channel=row["channel"],
        question=row["question"],
        bot_id=str(row["bot_id"]) if row.get("bot_id") else None,
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        kind=row.get("kind") or "question",
        status=row.get("status") or "active",
        config=row.get("config") or {},
    )


def get_schedules(bot_id: str | None = None, *, active_only: bool = False) -> list[Schedule]:
    """Fetch schedules from Supabase ordered by creation time.

    Args:
        bot_id: If provided, returns only schedules for that bot. Otherwise returns all schedules.
        active_only: If True, skips schedules that have ended (a finished PR reminder keeps its row with status != 'active').

    Returns:
        List of Schedule records.
    """
    conditions, params = [], []
    if bot_id is not None:
        conditions.append("(bot_id = %s OR bot_id IS NULL)")
        params.append(bot_id)
    if active_only:
        conditions.append("status = 'active'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM schedules{where} ORDER BY created_at", params)
        return [_row_to_schedule(r) for r in cur.fetchall()]


def get_schedule(id: str) -> Schedule | None:
    """Fetch one schedule by its UUID, whatever its kind or status, or None if it doesn't exist."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM schedules WHERE id = %s", (id,))
        row = cur.fetchone()
        return _row_to_schedule(row) if row else None


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
    *,
    bot_id: str,
) -> Schedule | None:
    """Update one or more fields of an existing schedule row owned by `bot_id`.

    Args:
        id: UUID of the schedule to update.
        bot_id: The calling bot; only its schedules (and legacy rows with no owner, which every
            bot can manage, matching get_schedules) are touched, so one bot's admin can't alter another's.
        cron: New cron expression; omit to leave unchanged.
        channel: New Slack channel ID; omit to leave unchanged.
        question: New question text; omit to leave unchanged.

    Returns:
        The updated Schedule record on success, or None if no fields were provided or the id
        was not found for this bot.
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

    params.extend([id, bot_id])
    sql = (
        f"UPDATE schedules SET {', '.join(fields)} WHERE id = %s AND (bot_id = %s OR bot_id IS NULL) AND kind = 'question' "
        "RETURNING id, cron, channel, question, bot_id, updated_at"
    )
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return _row_to_schedule(row) if row else None


def remove_schedule(id: str, *, bot_id: str) -> bool:
    """Delete a schedule row by its UUID, if it is owned by `bot_id` (or has no owner).

    Args:
        id: UUID of the schedule to remove.
        bot_id: The calling bot; same ownership rule as update_schedule.

    Returns:
        True if a row was deleted, False if no row matched the given id for this bot.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schedules WHERE id = %s AND (bot_id = %s OR bot_id IS NULL)", (id, bot_id))
        return cur.rowcount > 0


class AddedReminder(NamedTuple):
    """The reminder `add_pr_reminder` returns: the new row, or the equivalent one that already existed (`created` False)."""

    schedule: Schedule
    created: bool


def _same_reminder(existing: dict, new: dict) -> bool:
    """Whether two reminder configs on the same channel are the same job: same scope and the same people, and for a
    thread-scope reminder the same thread. The cron and lookback are not part of the identity."""
    return (
        existing.get("scope") == new["scope"]
        and sorted(existing.get("mentions") or []) == sorted(new["mentions"])
        and (new["scope"] != "thread" or existing.get("origin_thread_ts") == new["origin_thread_ts"])
    )


def add_pr_reminder(id: str, cron: str, channel: str, label: str, bot_id: str, config: dict, *, max_active: int) -> AddedReminder | None:
    """Insert a PR review reminder row, unless it already exists or the quota is used.

    Returns the new row; or, if the bot already has an active reminder on `channel` that is the same job (see
    `_same_reminder`), that existing row with `created` False and nothing inserted, so repeating a request doesn't
    double the pings; or None if the bot already has `max_active` other active ones on `channel`.

    The lookup and the insert share one transaction under an advisory lock keyed on (bot, channel), so two concurrent
    requests (the worker runs several replicas) can't both create the reminder or both pass the quota.

    `id` is chosen by the caller so the first reminder can show it. `label` is stored in `question` and is never sent
    to the LLM for this kind; `config` is a common.configs.ReminderConfig as a dict.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"pr_reminder:{bot_id}:{channel}",))
        cur.execute(
            f"SELECT {_COLUMNS} FROM schedules WHERE kind = 'pr_reminder' AND status = 'active' AND bot_id = %s AND channel = %s ORDER BY created_at",
            (bot_id, channel),
        )
        active = [_row_to_schedule(row) for row in cur.fetchall()]
        for existing in active:
            if _same_reminder(existing.config, config):
                return AddedReminder(existing, created=False)
        if len(active) >= max_active:
            return None
        cur.execute(
            f"INSERT INTO schedules (id, cron, channel, question, bot_id, kind, config) VALUES (%s, %s, %s, %s, %s, 'pr_reminder', %s) RETURNING {_COLUMNS}",
            (id, cron, channel, label, bot_id, Jsonb(config)),
        )
        return AddedReminder(_row_to_schedule(cur.fetchone()), created=True)


class AddedDatasetSync(NamedTuple):
    """The schedule `add_dataset_sync` returns: the new row, or the one that already syncs that dataset (`created` False)."""

    schedule: Schedule
    created: bool


def add_dataset_sync(id: str, cron: str, channel: str, label: str, bot_id: str, config: dict) -> AddedDatasetSync:
    """Insert a recurring dataset sync row, unless an active one for the same dataset already exists.

    A dataset is workspace-wide, so one recurring sync per dataset is enough whichever bot scheduled it: a second one
    would only run the same query twice. The lookup and the insert share one transaction under an advisory lock keyed
    on the dataset, so two concurrent requests can't both create it.

    `label` is stored in `question` and is never sent to the LLM for this kind; `config` is {"dataset": ..., "created_by": ...}.
    """
    dataset = config["dataset"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"dataset_sync:{dataset}",))
        cur.execute(f"SELECT {_COLUMNS} FROM schedules WHERE kind = 'dataset_sync' AND status = 'active' AND config->>'dataset' = %s", (dataset,))
        existing = cur.fetchone()
        if existing:
            return AddedDatasetSync(_row_to_schedule(existing), created=False)
        cur.execute(
            f"INSERT INTO schedules (id, cron, channel, question, bot_id, kind, config) VALUES (%s, %s, %s, %s, %s, 'dataset_sync', %s) RETURNING {_COLUMNS}",
            (id, cron, channel, label, bot_id, Jsonb(config)),
        )
        return AddedDatasetSync(_row_to_schedule(cur.fetchone()), created=True)


def get_dataset_sync_schedules() -> dict[str, Schedule]:
    """Active recurring dataset syncs by dataset name (one per dataset, see `add_dataset_sync`)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM schedules WHERE kind = 'dataset_sync' AND status = 'active' ORDER BY created_at")
        return {schedule.config.get("dataset", ""): schedule for schedule in (_row_to_schedule(row) for row in cur.fetchall())}


def set_schedule_status(id: str, status: str) -> None:
    """End an active schedule by moving it to a terminal status ('completed' or 'expired'); the row is kept (soft delete)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE schedules SET status = %s WHERE id = %s AND status = 'active'", (status, id))


def merge_schedule_config(id: str, patch: dict) -> None:
    """Merge `patch` into a schedule's `config` (top-level keys are replaced, the rest kept)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE schedules SET config = config || %s WHERE id = %s", (Jsonb(patch), id))
