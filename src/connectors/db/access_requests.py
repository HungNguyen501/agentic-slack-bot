"""Supabase access_requests table — submitted data access request CRUD."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from connectors.db.connection import connect


@dataclass
class AccessRequest:
    id: str
    bot_id: str
    request_type: str
    channel: str
    thread_ts: str
    reviewers: list[str]
    status: str
    ticket_id: str
    user_email: str
    principal_type: str
    # display_name/principal/filter_column/allowed_value/scope_column/scope_value are nullable
    # in the schema but always populated at insert time (add_access_request requires them) —
    # only service_principal_id/pr_url are genuinely unset until approval.
    display_name: str
    principal: str
    filter_column: str
    allowed_value: str
    scope_column: str
    scope_value: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    requester_id: str | None = None
    groups: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    service_principal_id: str | None = None
    pr_url: str | None = None


def _row_to_access_request(row: Any) -> AccessRequest:
    return AccessRequest(
        id=str(row["id"]),
        bot_id=row["bot_id"],
        request_type=row["request_type"],
        channel=row["channel"],
        thread_ts=row["thread_ts"],
        requester_id=row.get("requester_id"),
        reviewers=list(row.get("reviewers") or []),
        status=row["status"],
        ticket_id=row["ticket_id"],
        user_email=row["user_email"],
        principal_type=row["principal_type"],
        display_name=row["display_name"],
        principal=row["principal"],
        filter_column=row["filter_column"],
        allowed_value=row["allowed_value"],
        scope_column=row["scope_column"],
        scope_value=row["scope_value"],
        groups=list(row.get("groups") or []),
        tags=list(row.get("tags") or []),
        service_principal_id=row.get("service_principal_id"),
        pr_url=row.get("pr_url"),
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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
) -> AccessRequest:
    """Insert a newly submitted access request row with status='pending'.

    Returns:
        The created AccessRequest record. Includes the generated `id`, shown to the requester
        so reviewers can reference it unambiguously.
    """
    with connect() as conn, conn.cursor() as cur:
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
        return _row_to_access_request(cur.fetchone())


def get_access_request(bot_id: str, request_id: str, thread_ts: str) -> AccessRequest | None:
    """Fetch a specific access request by id, scoped to the thread it's being reviewed in.

    Scoping by thread_ts (in addition to id) prevents a reply in one thread from acting on a
    request that was submitted in a different thread.

    Args:
        bot_id: Bot the request was made through.
        request_id: The access_requests.id a reviewer referenced (e.g. "approve <request_id>").
        thread_ts: Slack thread timestamp the reply was posted in.

    Returns:
        The matching AccessRequest record, or None if no such request exists in this thread
        (including when request_id isn't a valid UUID).
    """
    with connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT * FROM access_requests WHERE bot_id = %s AND id = %s AND thread_ts = %s",
                (bot_id, request_id, thread_ts),
            )
        except psycopg.errors.InvalidTextRepresentation:
            conn.rollback()
            return None
        row = cur.fetchone()
        return _row_to_access_request(row) if row else None


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
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE access_requests SET status = %s, pr_url = COALESCE(%s, pr_url), "
            "service_principal_id = COALESCE(%s, service_principal_id), "
            "principal = COALESCE(%s, principal), display_name = COALESCE(%s, display_name) "
            "WHERE id = %s",
            (status, pr_url, service_principal_id, principal, display_name, id),
        )
