"""RQ worker tasks — resolved by dotted name (e.g. worker.tasks.reply_to_mention)."""
import logging
import os
import re

import httpx

from worker.agent import run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_API = "https://slack.com/api/chat.postMessage"

# Strip Slack user/bot mention tokens like <@U12345> from the start of a message
_MENTION_RE = re.compile(r"^(<@[^>]+>\s*)+", re.UNICODE)


def _post_slack_message(channel: str, thread_ts: str, text: str) -> str:
    """Post a message to a Slack thread and return its timestamp.

    Args:
        channel: Slack channel ID to post into.
        thread_ts: Timestamp of the parent message that anchors the thread.
        text: Message body in Slack mrkdwn format.

    Returns:
        The Slack message timestamp (ts) of the posted message.
    """
    resp = httpx.post(
        url=SLACK_API,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": channel,
            "thread_ts": thread_ts,
            "text": text,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")

    return data["ts"]


def reply_to_mention(channel: str, thread_ts: str, user: str | None = None, text: str = "") -> str:
    """Strip the @mention, run the agent, and post the answer back into the Slack thread.

    Args:
        channel: Slack channel ID where the mention occurred.
        thread_ts: Timestamp of the thread root used to scope conversation history.
        user: Slack user ID of the person who mentioned the bot; prefixed to the reply if provided.
        text: Raw message text including the @mention prefix.

    Returns:
        The Slack message timestamp (ts) of the posted reply.
    """
    # Strip the leading mention(s) so the agent only sees the actual question
    question = _MENTION_RE.sub("", text).strip()

    if not question:
        answer = (
            "Hi! Ask me anything about our Databricks catalogs, tables, columns, "
            "jobs, user's access control or data lineage."
        )

    else:
        log.info("Running agent for question: %.200s", question)
        try:
            answer = run_agent(question, thread_ts)
        except Exception as exc:
            log.exception("Agent error: %s", exc)
            answer = "Sorry, I ran into an error while processing your question. Please try again :hugging_face:."

    ts = _post_slack_message(channel, thread_ts, answer)
    log.info("Posted reply to %s (thread %s)", channel, thread_ts)
    return ts
