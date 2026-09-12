"""RQ worker tasks — resolved by dotted name (e.g. worker.tasks.reply_to_mention)."""
import logging
import re

from connectors import databricks, slack
from connectors.db.access_request_categories import channel_authorized, get_access_request_category
from connectors.db.access_requests import add_access_request
from connectors.db.bots import get_by_id as get_bot
from models.access_request_submission import VerifiedAccessRequest
from models.access_request_view import parse_emails
from worker import review
from worker.agent import run_agent, summarize_answer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

# Strip all Slack user/bot mention tokens like <@U12345> wherever they appear
_MENTION_RE = re.compile(r"<@[^>]+>\s*", re.UNICODE)

# Matches a UUID anywhere after the leading approve/reject keyword, so phrasing like
# "approve data access request ID=<uuid>" or "approve: <uuid>" works, not just the exact
# `approve <uuid>` form we ask for in the review message.
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _split_message(text: str, max_len: int = 3000) -> list[str]:
    """Split text into Slack-safe chunks that never exceed max_len characters.

    Code blocks are closed with a closing fence before a chunk boundary and
    reopened with the same fence (including any language specifier) at the start
    of the next chunk, so every chunk is valid Slack mrkdwn on its own.

    Args:
        text: The full message text to split, in Slack mrkdwn format.
        max_len: Maximum character length per chunk; defaults to 3 000 (Slack's safe limit).

    Returns:
        List of text chunks, each within max_len characters and properly fenced if split
        mid-code-block. Returns a single-element list when the text fits in one chunk.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    in_code_block = False
    code_fence = ""  # e.g. "```" or "```sql"

    for line in text.splitlines():
        line_len = len(line) + 1  # +1 for the joining newline

        if current_len + line_len > max_len and current_lines:
            if in_code_block:
                # Close the fence, flush, then reopen in the next chunk
                current_lines.append("```")
                chunks.append("\n".join(current_lines))
                current_lines = [code_fence]
                current_len = len(code_fence) + 1
            else:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_len = 0

        # Update fence state AFTER the split decision so the fence line itself
        # lands in the correct chunk
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_fence = line  # preserve language specifier e.g. "```sql"
            else:
                in_code_block = False
                code_fence = ""

        current_lines.append(line)
        current_len += line_len

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


def reply_to_mention(
    channel: str,
    thread_ts: str,
    bot_id: str,
    user: str | None = None,
    text: str = "",
) -> str:
    """Strip the @mention, run the agent, and post the answer back into the Slack thread.

    Args:
        channel: Slack channel ID where the mention occurred.
        thread_ts: Timestamp of the thread root used to scope conversation history.
        user: Slack user ID of the person who mentioned the bot; forwarded to the agent
            for schedule management authorization.
        text: Raw message text including the @mention prefix.
        bot_id: Bot identifier used to load per-bot config (token, skills, admin users).

    Returns:
        The Slack message timestamp (ts) of the last posted reply chunk.
    """
    bot = get_bot(bot_id)
    question = _MENTION_RE.sub("", text).strip()

    # Reviewers often copy-paste the exact reply we suggest, backticks and all (e.g.
    # "`approve <id>`") — strip surrounding backticks/punctuation before parsing.
    review_parts = question.split(maxsplit=1)
    first_word = review_parts[0].strip("`:,.").lower() if review_parts else ""
    if first_word in ("approve", "reject"):
        decision = first_word
        rest = review_parts[1] if len(review_parts) > 1 else ""

        # Dedupe case-insensitively so the same id repeated (copy-paste accidents) doesn't
        # trip this, but two genuinely different ids does — acting on just the first one
        # silently would be surprising if someone meant to reference two different requests.
        seen = set()
        distinct_ids = []
        for m in _REQUEST_ID_RE.findall(rest):
            if m.lower() not in seen:
                seen.add(m.lower())
                distinct_ids.append(m)

        if len(distinct_ids) > 1:
            result = "I found multiple request ids in that message — please approve or reject one at a time."
            return slack.post_message(channel, result, thread_ts, token=bot.bot_token)

        request_id = distinct_ids[0] if distinct_ids else None
        try:
            result = review.handle_review_decision(bot, channel, thread_ts, user, decision, request_id)
        except Exception as exc:
            log.exception("Review decision error: %s", exc)
            result = "Sorry, something went wrong while processing that review decision."
        return slack.post_message(channel, result, thread_ts, token=bot.bot_token)

    if not question:
        answer = (
            "Hi! Ask me anything about our Databricks catalogs, tables, columns, "
            "jobs, user's access control or data lineage."
        )
    else:
        log.info("Running agent for question: %.200s (bot_id=%s)", question, bot_id)
        try:
            answer = run_agent(question, thread_ts, channel, user_id=user, bot=bot)
        except Exception as exc:
            log.exception("Agent error: %s", exc)
            answer = "Sorry, I ran into an error while processing your question. Please try again :hugging_face:."

    if not answer:
        # run_agent returns "" when a tool call already posted everything the user needs
        # (e.g. the data access request button) — nothing left to reply with.
        return thread_ts

    chunks = _split_message(answer)
    ts = thread_ts
    for chunk in chunks:
        ts = slack.post_message(channel, chunk, thread_ts, token=bot.bot_token)
    log.info("Posted reply (%d chunk(s)) to %s (thread %s)", len(chunks), channel, thread_ts)
    return ts


def process_scheduled_question(channel: str, question: str, bot_id: str, **kwargs) -> str:
    """Run the agent for a scheduled question, then post the question with a results summary
    as the thread root and the full answer as a reply inside that thread.

    The thread root is posted first (as a placeholder) so its timestamp can be used as the
    history key while the agent runs; it's then edited in place once the answer and summary
    are ready. Using the real thread timestamp from the start means the thread can be used
    to continue the conversation with follow-up @mentions afterward.

    Args:
        channel: Slack channel ID to post into.
        question: The question text to send to the agent.
        bot_id: Bot identifier used to load per-bot config (token, skills, admin users).

    Returns:
        The Slack message timestamp (ts) of the agent's detailed reply.
    """
    bot = get_bot(bot_id)
    log.info("Running scheduled agent for channel=%s question=%.200s (bot_id=%s)", channel, question, bot_id)

    header_ts = slack.post_message(channel, f"*Scheduled question:* _{question}_", token=bot.bot_token)

    try:
        answer = run_agent(question, header_ts, channel, bot=bot)
        summary = summarize_answer(question, answer)
    except Exception as exc:
        log.exception("Scheduled agent error: %s", exc)
        answer = "Sorry, I ran into an error while processing the scheduled question."
        summary = answer

    slack.update_message(
        channel,
        header_ts,
        f"*Scheduled question:* _{question}_\n\n{summary}",
        token=bot.bot_token,
    )

    chunks = _split_message(answer)
    ts = header_ts
    for chunk in chunks:
        ts = slack.post_message(channel, chunk, header_ts, token=bot.bot_token)
    log.info("Posted scheduled answer (%d chunk(s)) to %s (thread %s)", len(chunks), channel, header_ts)
    return ts


def notify_access_request_button_expired(bot_id: str, channel: str, thread_ts: str) -> None:
    """Tell the thread that a clicked "Open Form" button is past its TTL and won't open.

    Args:
        bot_id: Bot identifier used to load per-bot config (token).
        channel: Slack channel ID the button was posted into.
        thread_ts: Thread timestamp to reply into.
    """
    bot = get_bot(bot_id)
    slack.post_message(
        channel,
        "This request button has expired. Please ask again to get a new one.",
        thread_ts,
        token=bot.bot_token,
    )


def process_data_access_submission(
    bot_id: str,
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
        bot_id: Bot identifier used to load per-bot config (token).
        channel: Slack channel ID the original request thread lives in.
        thread_ts: Timestamp of the thread to post the review request into.
        requester_id: Slack user ID who submitted the form.
        request_type: The access request category that was submitted.
        reviewers: Slack user IDs to notify for review.
        fields: Flattened {block_id: value} dict extracted from the modal's view.state.values.

    Returns:
        The Slack message timestamp (ts) of the last posted review-request chunk.
    """
    bot = get_bot(bot_id)
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
    category = get_access_request_category(bot_id, request_type)
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
        lookup = databricks.find_service_principals_by_emails(emails)
    else:
        lookup = databricks.find_users_by_emails(emails)

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
            bot_id=bot_id,
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
    for chunk in _split_message(message):
        ts = slack.post_message(channel, chunk, thread_ts, token=bot.bot_token)
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
