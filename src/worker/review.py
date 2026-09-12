"""Deterministic reviewer approve/reject handling for data access requests.

This bypasses the LLM tool-calling loop entirely (see reply_to_mention in tasks.py) — the
user chose exact-keyword detection over LLM-inferred intent, so this is plain, testable
control flow rather than another agent tool.
"""
import logging
from datetime import UTC, datetime

from connectors import databricks, github
from connectors.db.access_request_categories import channel_authorized, get_access_request_category
from connectors.db.access_requests import AccessRequest, get_access_request, update_access_request_status
from connectors.db.bots import BotConfig
from models.access_control_rules import format_rules_entry, format_service_principals_snapshot
from models.access_request_pr import build_pr
from models.access_request_submission import VerifiedAccessRequest

log = logging.getLogger("worker.review")


def handle_review_decision(
    bot: BotConfig,
    channel: str,
    thread_ts: str,
    user_id: str | None,
    decision: str,
    request_id: str | None,
) -> str:
    """Act on an "approve <request_id>"/"reject <request_id>" reply.

    Args:
        bot: Per-bot config (bot_id used to scope the Supabase lookup).
        channel: Slack channel the reply was posted in.
        thread_ts: Thread timestamp — a request_id must belong to this thread to be actioned.
        user_id: Slack user ID of the person replying.
        decision: Either "approve" or "reject" (already lowercased/stripped by the caller).
        request_id: The access_requests.id the reviewer referenced, or None if they replied
            with the bare keyword and no id.

    Returns:
        A reply string — always something concrete (missing id, invalid/foreign id, already
        handled, expired, wrong reviewer, rejected, or approved). The caller only reaches this
        function once the message already starts with "approve"/"reject", so there's no
        ambiguous case to fall through to the normal agent flow for.
    """
    if not request_id:
        return "Please include the request id, e.g. `approve <request_id>` or `reject <request_id>`."

    row = get_access_request(bot.bot_id, request_id, thread_ts)
    if not row:
        return f"No pending request found with id `{request_id}` in this thread."

    # Terminal states are reported regardless of who's asking (requester or reviewer) —
    # nothing left to do either way, so this is checked before the reviewer-only gate below.
    if row.status != "pending":
        return f"Request `{request_id}` has already been {row.status} and can't be changed."

    if datetime.now(UTC) > row.expires_at:
        return (
            f"Request `{request_id}` expired at {row.expires_at.strftime('%Y-%m-%d %H:%M UTC')} "
            "(24h limit) and can no longer be approved or rejected by anyone. Please submit a new request."
        )

    if user_id not in row.reviewers:
        return "You are not listed as a reviewer for this request."

    if decision == "reject":
        update_access_request_status(row.id, "rejected")
        return f"Request `{request_id}` rejected by <@{user_id}>."

    return _approve(bot, row, channel, user_id)


def _approve(bot: BotConfig, row: AccessRequest, channel: str, approver_id: str) -> str:
    """Resolve the principal, open the data-platform PR, and mark the request approved."""
    # Re-validate against access_request_categories rather than trusting the submission-time
    # snapshot — the category or this channel's entry in it may have been revoked since the
    # request was submitted, and approval is what actually writes to the data-platform repo.
    category = get_access_request_category(bot.bot_id, row.request_type)
    if not channel_authorized(category, row.channel):
        return (
            f"Sorry, this channel is no longer configured for `{row.request_type}` requests, "
            f"so I can't approve `{row.id}`. Please reach out to your data team directly."
        )

    service_principal_id = None

    if row.principal_type == "Service principals":
        sp = databricks.find_or_create_service_principal(row.user_email, row.groups)
        display_name, principal, service_principal_id = sp["displayName"], sp["applicationId"], sp["id"]
    else:
        # Submission only warned if the user wasn't found (so the request could still reach
        # review) — re-verify for real here, since approval is what actually writes the
        # principal into the data-platform repo. Never write a "does not exist" placeholder.
        record = databricks.find_user_by_email(row.user_email)
        if not record:
            return (
                f"Cannot approve `{row.id}` — user `{row.user_email}` still doesn't exist in Databricks. "
                "Verify the email and try approving again once it does."
            )
        display_name, principal = record.get("userName", row.user_email), row.user_email

    verified = VerifiedAccessRequest(
        ticket_id=row.ticket_id,
        display_name=display_name,
        user_email=row.user_email,
        principal=principal,
        filter_column=row.filter_column,
        allowed_value=row.allowed_value,
        scope_column=row.scope_column,
        scope_value=row.scope_value,
        principal_type=row.principal_type,
        groups=row.groups,
        tags=row.tags,
    )

    rules_entry = format_rules_entry(verified)
    sp_snapshot = (
        format_service_principals_snapshot(databricks.list_service_principals())
        if row.principal_type == "Service principals"
        else None
    )
    pr_title, pr_body = build_pr(verified, requester_id=row.requester_id, approver_id=approver_id)

    pr_url = github.open_data_access_pr(row.ticket_id, rules_entry, sp_snapshot, pr_title, pr_body)

    update_access_request_status(
        row.id,
        "approved",
        pr_url=pr_url,
        service_principal_id=service_principal_id,
        principal=principal,
        display_name=display_name,
    )
    log.info("Access request %s approved by %s; PR: %s", row.id, approver_id, pr_url)
    return f"Request `{row.id}` approved by <@{approver_id}>. PR opened: {pr_url}"
