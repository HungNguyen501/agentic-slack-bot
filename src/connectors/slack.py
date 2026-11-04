"""Slack Web API client — message posting, history reads, modal views, and mrkdwn chunking."""
from collections.abc import Callable
from typing import Any

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


def _collect_pages(fetch: Callable[[str | None], Any], max_messages: int | None = None) -> tuple[list[dict], bool]:
    """Follow Slack cursor pagination, calling `fetch(cursor)` until the last page or `max_messages` is reached.

    Returns the messages and whether some were left unread because of `max_messages`.
    """
    messages: list[dict] = []
    cursor = None
    while True:
        response = fetch(cursor)
        messages.extend(response["messages"])
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if max_messages is not None and len(messages) > max_messages:
            return messages[:max_messages], True
        if not cursor:
            return messages, False
        if max_messages is not None and len(messages) == max_messages:
            return messages, True


def conversations_replies(channel: str, ts: str, *, token: str) -> list[dict]:
    """Fetch every message in a thread, root included (needs the channels:history / groups:history scope).

    Args:
        channel: Slack channel ID containing the thread.
        ts: Timestamp of the thread's root message.
        token: Bot OAuth token (xoxb-...) to authenticate the request.

    Returns:
        The thread's messages, oldest first.
    """
    client = WebClient(token=token)
    return _collect_pages(lambda cursor: client.conversations_replies(channel=channel, ts=ts, limit=200, cursor=cursor))[0]


def conversations_history(channel: str, oldest: float, *, token: str, max_messages: int) -> tuple[list[dict], bool]:
    """Fetch top-level channel messages posted after `oldest` (needs the channels:history / groups:history scope).

    Args:
        channel: Slack channel ID to read.
        oldest: Unix time; only messages newer than this are returned.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
        max_messages: Stop paginating once this many messages were collected.

    Returns:
        At most `max_messages` messages, newest first, and whether older ones in the window were left unread. Thread
        replies are not included, only thread roots.
    """
    client = WebClient(token=token)
    return _collect_pages(
        lambda cursor: client.conversations_history(channel=channel, oldest=f"{oldest:.6f}", limit=200, cursor=cursor),
        max_messages,
    )


def get_bot_user_id(token: str) -> str:
    """Return the Slack user ID the bot token belongs to (what an @mention of the bot looks like)."""
    return str(WebClient(token=token).auth_test()["user_id"])


def get_permalink(channel: str, message_ts: str, *, token: str) -> str:
    """Return the permanent link to a message."""
    return str(WebClient(token=token).chat_getPermalink(channel=channel, message_ts=message_ts)["permalink"])


def open_view(trigger_id: str, view: dict, *, token: str) -> None:
    """Open a Slack modal. Must be called within ~3 seconds of the trigger_id being issued.

    Args:
        trigger_id: Trigger ID from the interaction (e.g. a button click) that authorizes the modal.
        view: Slack view payload, e.g. as built by agent.forms.access_request_view.build_access_request_view.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).views_open(trigger_id=trigger_id, view=view)


_FENCE = "```"
_CLOSE_COST = len("\n" + _FENCE)  # what closing an open code block adds to a chunk


def _next_fence(line: str, fence: str | None) -> str | None:
    """Code-block state after `line`: the opening fence (with any language tag) if inside one, else None.

    Counts every ``` on the line, so a complete one-line block like ```x``` leaves the state unchanged.
    """
    if line.count(_FENCE) % 2 == 0:
        return fence
    if fence is not None:
        return None
    return _FENCE + line[line.rindex(_FENCE) + len(_FENCE):].strip() if line.startswith(_FENCE) else _FENCE


class _Chunker:
    """Accumulates lines into chunks of at most max_len characters, closing and reopening code fences at each boundary."""

    def __init__(self, max_len: int) -> None:
        self.max_len = max_len
        self.chunks: list[str] = []
        self.fence: str | None = None  # opening fence of the code block open at the end of the current chunk
        self._lines: list[str] = []
        self._length = 0
        self._content = 0  # lines added since the last flush, not counting a reopened fence

    def add(self, line: str, after: str | None) -> None:
        """Append `line`, flushing first if it (plus a closing fence, if it leaves a block open) wouldn't fit."""
        closing = _CLOSE_COST if after is not None else 0
        if self._content and self._length + self._joined(line) + closing > self.max_len:
            self._flush()
        self._length += self._joined(line)
        self._lines.append(line)
        self._content += 1
        self.fence = after

    def finish(self) -> list[str]:
        if self._content:
            self.chunks.append("\n".join(self._lines))
        return self.chunks

    def _joined(self, line: str) -> int:
        return len(line) + (1 if self._lines else 0)

    def _flush(self) -> None:
        if self.fence is not None:
            self._lines.append(_FENCE)
        self.chunks.append("\n".join(self._lines))
        self._lines = [self.fence] if self.fence is not None else []
        self._length = len(self.fence) if self.fence is not None else 0
        self._content = 0


def split_message(text: str, max_len: int = 3000) -> list[str]:
    """Split text into Slack-safe chunks that never exceed max_len characters.

    Code blocks are closed with a closing fence before a chunk boundary and
    reopened with the same fence (including any language specifier) at the start
    of the next chunk, so every chunk is valid Slack mrkdwn on its own. A single
    line too long for one chunk is cut into fragments, each sized to leave room
    for the fences.

    Args:
        text: The full message text to split, in Slack mrkdwn format.
        max_len: Maximum character length per chunk; defaults to 3 000 (Slack's safe limit).
            Must leave room for the fences, i.e. be comfortably larger than a few dozen characters.

    Returns:
        List of text chunks, each within max_len characters and properly fenced if split
        mid-code-block. Returns a single-element list when the text fits in one chunk.
    """
    if len(text) <= max_len:
        return [text]

    chunker = _Chunker(max_len)
    for line in text.splitlines():
        after = _next_fence(line, chunker.fence)
        reopen = len(chunker.fence) + 1 if chunker.fence is not None else 0
        if reopen + len(line) + (_CLOSE_COST if after is not None else 0) <= max_len:
            chunker.add(line, after)
            continue

        in_block = chunker.fence is not None or after is not None
        size = max(1, max_len - reopen - (_CLOSE_COST if in_block else 0))
        *head, last = (line[i:i + size] for i in range(0, len(line), size))
        for fragment in head:
            chunker.add(fragment, chunker.fence)
        chunker.add(last, after)
    return chunker.finish()
