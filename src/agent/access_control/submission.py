"""Persisting a submitted data access request form and posting it back into the thread for review."""
import logging

from agent.access_control import principals
from agent.access_control.access_request_categories import channel_authorized, get_access_request_category
from agent.access_control.access_requests import add_access_request
from agent.forms.access_request_submission import VerifiedAccessRequest
from agent.forms.access_request_view import parse_emails
from agent.history import record_thread_messages
from connectors import slack
from connectors.db.bots import BotConfig

log = logging.getLogger("agent.access_control.submission")


def submit_data_access_request(
    bot: BotConfig,
    channel: str,
    thread_ts: str,
    requester_id: str | None,
    request_type: str,
    reviewers: list[str],
    fields: dict,
) -> str:
    """Log a submitted data access request form and post it back into the requesting thread for review.

    The form's user_emails field can hold multiple comma/newline-separated addresses; this
    fans out into one independent access_requests row per email (sharing the rest of the
    form's fields), each with its own id — reviewers approve/reject them individually via the
    same "approve <id>" mechanism used for any single request.

    Args:
        bot: Per-bot config (bot_id scopes the Supabase rows, bot_token posts to Slack).
        channel: Slack channel ID the original request thread lives in.
        thread_ts: Timestamp of the thread to post the review request into.
        requester_id: Slack user ID who submitted the form.
        request_type: The access request category that was submitted.
        reviewers: Slack user IDs to notify for review.
        fields: Flattened {block_id: value} dict extracted from the modal's view.state.values.

    Returns:
        The Slack message timestamp (ts) of the last posted review-request chunk.
    """
    log.info(
        "Data access request submitted: request_type=%s requester_id=%s fields=%s",
        request_type,
        requester_id,
        fields,
    )

    # Re-validate against access_request_categories rather than trusting the button's
    # baked-in snapshot from when the form was first offered — the category or this
    # channel's entry in it may have been revoked in the time since the button was clicked,
    # and this is the point where the request actually gets persisted and reviewer-visible.
    category = get_access_request_category(bot.bot_id, request_type)
    if not channel_authorized(category, channel):
        return slack.post_message(
            channel,
            "Sorry, data access requests of this type aren't available in this channel anymore. "
            "Please reach out to your data team directly.",
            thread_ts,
            token=bot.bot_token,
        )

    ticket_id = (fields.get("ticket_id") or "").strip()
    principal_type = fields.get("principal_type", "")
    emails = parse_emails(fields.get("user_emails") or "")

    # One batch lookup for the whole submission rather than one full SCIM listing per email —
    # each listing paginates the entire workspace, so doing it per-email is what made
    # multi-email submissions time out (RQ's 30s job_timeout) once there was more than one.
    if principal_type == "Service principals":
        lookup = principals.find_service_principals_by_emails(emails)
    else:
        lookup = principals.find_users_by_emails(emails)

    reviewer_mentions = ", ".join(f"<@{r}>" for r in reviewers) or "the data team"
    sections = []

    for index, user_email in enumerate(emails, start=1):
        display_name, principal, principal_missing = _resolve_principal(user_email, principal_type, lookup.get(user_email))

        verified = VerifiedAccessRequest(
            ticket_id=ticket_id,
            display_name=display_name,
            user_email=user_email,
            principal=principal,
            filter_column=fields.get("filter_column", ""),
            allowed_value=fields.get("allowed_value", ""),
            scope_column=fields.get("scope_column", ""),
            scope_value=fields.get("scope_value", ""),
            principal_type=principal_type,
            groups=fields.get("groups") or [],
            tags=fields.get("tags") or [],
        )

        row = add_access_request(
            bot_id=bot.bot_id,
            request_type=request_type,
            channel=channel,
            thread_ts=thread_ts,
            requester_id=requester_id,
            reviewers=reviewers,
            ticket_id=ticket_id,
            user_email=user_email,
            principal_type=principal_type,
            display_name=display_name,
            principal=principal,
            filter_column=fields.get("filter_column", ""),
            allowed_value=fields.get("allowed_value", ""),
            scope_column=fields.get("scope_column", ""),
            scope_value=fields.get("scope_value", ""),
            groups=fields.get("groups") or [],
            tags=fields.get("tags") or [],
        )
        request_id = row.id
        expires_line = f"expires_at: {row.expires_at.strftime('%Y-%m-%d %H:%M UTC')}"

        warning_line = (
            f":warning: The user `{user_email}` was not found in Databricks — please verify before approving.\n"
            if principal_missing
            else ""
        )
        sections.append(
            f"*Request {index} of {len(emails)}* (id: `{request_id}`)\n"
            f"{verified.to_mrkdwn(extra_lines=[expires_line])}\n"
            f"{warning_line}"
            f"Reply `@{bot.bot_id} approve data access request {request_id}` to approve, or "
            f"`@{bot.bot_id} reject data access request {request_id}` to reject."
        )

    header = (
        f"*Data Access Request* — {len(emails)} sub-request(s) for ticket `{ticket_id}`\n"
        f"User <@{requester_id}> requests adding data access for the following:\n\n"
    )
    footer = f"\nPlease help review: {reviewer_mentions}"
    divider = "\n" + "─" * 24 + "\n\n"
    message = header + divider.join(sections) + footer

    ts = thread_ts
    for chunk in slack.split_message(message):
        ts = slack.post_message(channel, chunk, thread_ts, token=bot.bot_token)
    record_thread_messages(thread_ts, {"role": "assistant", "content": message})
    return ts


def _resolve_principal(user_email: str, principal_type: str, record: dict | None) -> tuple[str, str, bool]:
    """Soft-resolve a submitted email to its Databricks display_name/principal.

    Args:
        user_email: The submitted email.
        principal_type: "Service principals" or "Users", from the form.
        record: This email's pre-fetched SCIM match (from a batch lookup covering every
            email in the submission), or None if no match was found.

    Returns:
        (display_name, principal, principal_missing) — principal_missing is True only for
        the "Users" path (users aren't created by this bot, so a missing one is flagged as a
        warning rather than a hard refusal; approval re-verifies for real — see review.py).
        The "Service principals" path is never "missing": this workflow is what creates the
        principal on approval, so not finding one yet is expected, not a warning.
    """
    if principal_type == "Service principals":
        if record:
            return record["displayName"], record["applicationId"], False
        return f"svc-{user_email}", "(created on approval)", False

    if record:
        return record.get("userName", user_email), user_email, False
    return user_email, "(does not exist)", True
