"""Slack Web API client — message posting, modal views, and mrkdwn chunking."""
from slack_sdk import WebClient


def post_message(channel: str, text: str, thread_ts: str | None = None, blocks: list[dict] | None = None, *, token: str) -> str:
    """Post a message to Slack and return its timestamp.

    Args:
        channel: Slack channel ID to post into.
        text: Message body in Slack mrkdwn format (also used as the notification fallback text when blocks are set).
        thread_ts: If provided, posts as a reply in that thread; otherwise posts a new top-level message.
        blocks: Optional Block Kit blocks to render instead of/alongside plain text.
        token: Bot OAuth token (xoxb-...) to authenticate the request.

    Returns:
        The Slack message timestamp (ts) of the posted message.
    """
    response = WebClient(token=token).chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, blocks=blocks)
    return str(response["ts"])


def update_message(channel: str, ts: str, text: str, *, token: str) -> None:
    """Edit an existing Slack message in place.

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to update.
        text: New message body in Slack mrkdwn format.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).chat_update(channel=channel, ts=ts, text=text)


def delete_message(channel: str, ts: str, *, token: str) -> None:
    """Delete an existing Slack message (e.g. a "thinking..." placeholder no longer needed).

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to delete.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).chat_delete(channel=channel, ts=ts)


def open_view(trigger_id: str, view: dict, *, token: str) -> None:
    """Open a Slack modal. Must be called within ~3 seconds of the trigger_id being issued.

    Args:
        trigger_id: Trigger ID from the interaction (e.g. a button click) that authorizes the modal.
        view: Slack view payload, e.g. as built by agent.forms.access_request_view.build_access_request_view.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).views_open(trigger_id=trigger_id, view=view)


def split_message(text: str, max_len: int = 3000) -> list[str]:
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
