"""Supabase access_request_categories table — per-bot/channel eligibility for data access requests."""
from dataclasses import dataclass, field
from typing import Any

from connectors.db.connection import connect


@dataclass
class AccessRequestCategory:
    id: str
    bot_id: str
    request_type: str
    channel_ids: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)


def _row_to_category(row: Any) -> AccessRequestCategory:
    return AccessRequestCategory(
        id=str(row["id"]),
        bot_id=str(row["bot_id"]),
        request_type=row["request_type"],
        channel_ids=list(row.get("channel_ids") or []),
        reviewers=list(row.get("reviewers") or []),
    )


def get_access_request_category(bot_id: str, request_type: str) -> AccessRequestCategory | None:
    """Fetch the access-control row gating a data access request type for a bot.

    Args:
        bot_id: Bot identifier the request was made through.
        request_type: The requested access category, e.g. "table_row_filter_access".

    Returns:
        AccessRequestCategory record, or None if no category is configured for this
        bot_id/request_type pair.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, bot_id, request_type, channel_ids, reviewers FROM access_request_categories "
            "WHERE bot_id = %s AND request_type = %s",
            (bot_id, request_type),
        )
        row = cur.fetchone()
        return _row_to_category(row) if row else None


def channel_authorized(category: AccessRequestCategory | None, channel: str) -> bool:
    """True if `category` (from get_access_request_category) permits requests in `channel`.

    Shared by the three places that must independently re-check this — asking for the form
    (worker/agent.py), persisting a submission (worker/tasks.py), and approving (worker/review.py)
    — since a category can be revoked at any point between those steps.
    """
    return bool(category) and channel in category.channel_ids
