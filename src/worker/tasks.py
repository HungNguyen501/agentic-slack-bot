"""RQ worker tasks — resolved by dotted name (e.g. worker.tasks.reply_to_mention)."""
import logging
import os
import re

import httpx

from worker.agent import run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
# Strip all Slack user/bot mention tokens like <@U12345> wherever they appear
_MENTION_RE = re.compile(r"<@[^>]+>\s*", re.UNICODE)


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


def _post_slack_message(channel: str, text: str, thread_ts: str | None = None) -> str:
    """Post a message to Slack and return its timestamp.

    Args:
        channel: Slack channel ID to post into.
        text: Message body in Slack mrkdwn format.
        thread_ts: If provided, posts as a reply in that thread; otherwise posts a new top-level message.

    Returns:
        The Slack message timestamp (ts) of the posted message.
    """
    payload: dict = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    resp = httpx.post(
        url="https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
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
        user: Slack user ID of the person who mentioned the bot; forwarded to the agent
            for schedule management authorization.
        text: Raw message text including the @mention prefix.

    Returns:
        The Slack message timestamp (ts) of the last posted reply chunk.
    """
    question = _MENTION_RE.sub("", text).strip()

    if not question:
        answer = (
            "Hi! Ask me anything about our Databricks catalogs, tables, columns, "
            "jobs, user's access control or data lineage."
        )
    else:
        log.info("Running agent for question: %.200s", question)
        try:
            answer = run_agent(question, thread_ts, user_id=user)
        except Exception as exc:
            log.exception("Agent error: %s", exc)
            answer = "Sorry, I ran into an error while processing your question. Please try again :hugging_face:."

    chunks = _split_message(answer)
    ts = thread_ts
    for chunk in chunks:
        ts = _post_slack_message(channel, chunk, thread_ts)
    log.info("Posted reply (%d chunk(s)) to %s (thread %s)", len(chunks), channel, thread_ts)
    return ts


def process_scheduled_question(channel: str, question: str, **kwargs) -> str:
    """Run the agent for a scheduled question and post the result as a new Slack thread.

    Posts the question as a top-level message, then replies with the agent's answer in
    that thread. The thread timestamp is used as the history key so each scheduled run
    gets a fresh conversation context.

    Args:
        channel: Slack channel ID to post into.
        question: The question text to send to the agent.

    Returns:
        The Slack message timestamp (ts) of the agent's reply.
    """
    log.info("Running scheduled agent for channel=%s question=%.200s", channel, question)

    header_ts = _post_slack_message(channel, f"*Scheduled question:* _{question}_")

    try:
        answer = run_agent(question, header_ts)
    except Exception as exc:
        log.exception("Scheduled agent error: %s", exc)
        answer = "Sorry, I ran into an error while processing the scheduled question."

    chunks = _split_message(answer)
    ts = header_ts
    for chunk in chunks:
        ts = _post_slack_message(channel, chunk, header_ts)
    log.info("Posted scheduled answer (%d chunk(s)) to %s (thread %s)", len(chunks), channel, header_ts)
    return ts
