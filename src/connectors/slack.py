"""Slack Web API client — message posting, history reads, modal views, and mrkdwn chunking."""
import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from redis import RedisError
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from connectors.redis_client import client as redis_client

log = logging.getLogger("connectors.slack")

# An app whose token lacks chat:write.customize is remembered in Redis (`slack_no_customize:{sha256(token)[:16]}`), not in
# memory: `rq worker` forks a process per job, so a module-level set would be forgotten after every message or run.
# Without it every message from such an app costs two Slack calls (the refused attempt, then the retry) and a warning.
# It expires so that adding the scope and reinstalling the app takes effect without a restart.
_NO_CUSTOMIZE_TTL_SECONDS = 600


def _no_customize_key(token: str) -> str:
    return f"slack_no_customize:{hashlib.sha256(token.encode()).hexdigest()[:16]}"


def _lacks_customize_scope(token: str) -> bool:
    try:
        return bool(redis_client.exists(_no_customize_key(token)))
    except RedisError:
        return False


def _remember_missing_customize_scope(token: str) -> None:
    try:
        redis_client.set(_no_customize_key(token), "1", ex=_NO_CUSTOMIZE_TTL_SECONDS)
    except RedisError:
        log.warning("Could not remember that this Slack app lacks chat:write.customize", exc_info=True)


def post_message(
    channel: str,
    text: str,
    thread_ts: str | None = None,
    blocks: list[dict] | None = None,
    *,
    token: str,
    username: str | None = None,
    icon_emoji: str | None = None,
    icon_url: str | None = None,
) -> str:
    """Post a message to Slack and return its timestamp.

    Args:
        channel: Slack channel ID to post into.
        text: Message body in Slack mrkdwn format (also used as the notification fallback text when blocks are set).
        thread_ts: If provided, posts as a reply in that thread; otherwise posts a new top-level message.
        blocks: Optional Block Kit blocks to render instead of/alongside plain text.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
        username: Sender name to show instead of the bot's own. Needs the chat:write.customize scope; without it the
            message is posted under the bot's own name and icon.
        icon_emoji: Emoji (":name:") to show as the sender's icon, instead of the bot's own. Same scope as `username`.
        icon_url: Image URL to show as the sender's icon. Same scope as `username`.

    Returns:
        The Slack message timestamp (ts) of the posted message.
    """
    client = WebClient(token=token)
    identity = {key: value for key, value in (("username", username), ("icon_emoji", icon_emoji), ("icon_url", icon_url)) if value}
    if identity and _lacks_customize_scope(token):
        identity = {}
    try:
        response = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, blocks=blocks, **identity)
    except SlackApiError as exc:
        if not identity or exc.response.get("error") != "missing_scope":
            raise
        # A message is better than none: the app lacks chat:write.customize, so post it as the bot itself.
        response = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, blocks=blocks)
        # Only now is the missing scope proven: had the retry failed too, the cause could be something else.
        _remember_missing_customize_scope(token)
        log.warning("Slack app lacks chat:write.customize; posted as the bot instead of %s (remembered for %d s)", username, _NO_CUSTOMIZE_TTL_SECONDS)
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


def add_reaction(channel: str, ts: str, name: str, *, token: str) -> None:
    """Add an emoji reaction to a message as the bot.

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to react to.
        name: Emoji name without colons (e.g. "white_check_mark").
        token: Bot OAuth token (xoxb-...) to authenticate the request.

    Raises:
        SlackApiError: with error `already_reacted` when the bot already left this reaction, `missing_scope` when the app
            lacks `reactions:write`, or another Slack error code.
    """
    WebClient(token=token).reactions_add(channel=channel, timestamp=ts, name=name)


def delete_message(channel: str, ts: str, *, token: str) -> None:
    """Delete an existing Slack message (e.g. a "thinking..." placeholder no longer needed).

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to delete.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).chat_delete(channel=channel, ts=ts)


# How often one page may be asked for again when the caller's `retry` keeps saying yes.
_MAX_PAGE_RETRIES = 3


def _collect_pages(
    fetch: Callable[[str | None], Any], max_messages: int | None = None, retry: Callable[[SlackApiError], bool] | None = None
) -> tuple[list[dict], bool]:
    """Follow Slack cursor pagination, calling `fetch(cursor)` until the last page or `max_messages` is reached.

    When a page fails and `retry` is given, `retry(error)` says whether to ask for that same page again (it does any waiting; a page
    is asked for again at most `_MAX_PAGE_RETRIES` times). If it says no, the pages read so far are returned as not finished; with
    none read, the error is raised.

    Returns the messages and whether some were left unread (because of `max_messages`, or a page that could not be read).
    """
    messages: list[dict] = []
    cursor = None
    failures = 0
    while True:
        try:
            response = fetch(cursor)
        except SlackApiError as exc:
            if retry is None:
                raise
            failures += 1
            if failures <= _MAX_PAGE_RETRIES and retry(exc):
                continue
            if not messages:
                raise
            return messages, True
        failures = 0
        messages.extend(response["messages"])
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if max_messages is not None and len(messages) > max_messages:
            return messages[:max_messages], True
        if not cursor:
            return messages, False
        if max_messages is not None and len(messages) == max_messages:
            return messages, True


def conversations_replies(
    channel: str, ts: str, *, token: str, max_messages: int | None = None, retry: Callable[[SlackApiError], bool] | None = None
) -> list[dict]:
    """Fetch the messages of a thread, root included (needs the channels:history / groups:history scope).

    Args:
        channel: Slack channel ID containing the thread.
        ts: Timestamp of the thread's root message.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
        max_messages: Stop paginating once this many messages were collected; None reads the whole thread.
        retry: Asked when a page fails whether to try that page again (see `_collect_pages`); when it says no after some pages
            were read, those are returned and the rest of the thread is simply not in the result.

    Returns:
        The thread's messages, oldest first, at most `max_messages` of them when that is given.
    """
    client = WebClient(token=token)
    limit = 200 if max_messages is None else min(200, max_messages)
    return _collect_pages(lambda cursor: client.conversations_replies(channel=channel, ts=ts, limit=limit, cursor=cursor), max_messages, retry)[0]


def conversations_history(channel: str, oldest: float, *, token: str, max_messages: int, latest: str | None = None) -> tuple[list[dict], bool]:
    """Fetch top-level channel messages posted after `oldest` (needs the channels:history / groups:history scope).

    Args:
        channel: Slack channel ID to read.
        oldest: Unix time; only messages newer than this are returned.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
        max_messages: Stop paginating once this many messages were collected.
        latest: A Slack ts; only messages older than this are returned (exclusive). Passed through as is, so a ts read
            from an earlier call keeps its microseconds.

    Returns:
        At most `max_messages` messages, newest first, and whether older ones in the window were left unread. Thread
        replies are not included, only thread roots.
    """
    client = WebClient(token=token)
    extra = {"latest": latest} if latest is not None else {}
    return _collect_pages(
        lambda cursor: client.conversations_history(channel=channel, oldest=f"{oldest:.6f}", limit=min(200, max_messages), cursor=cursor, **extra),
        max_messages,
    )


def get_channel_info(channel: str, *, token: str) -> dict:
    """Return Slack's description of a channel: `is_private`, `is_archived`, `is_member`, `is_im`, `name`... (needs channels:read / groups:read)."""
    return dict(WebClient(token=token).conversations_info(channel=channel)["channel"])


class ChannelListIncompleteError(Exception):
    """The channel list has more pages than were read, so a channel not found in them may still exist."""


def find_channel_id(name: str, *, token: str, max_pages: int = 20) -> str | None:
    """Return the ID of the non-archived public or private channel called `name` (lower case, no `#`), or None (needs channels:read / groups:read).

    Only channels the bot can see are listed, so a private channel it is not in looks like a missing one.

    Raises:
        ChannelListIncompleteError: `max_pages` pages were read and the list goes on; None is reserved for a listing read to its end.
    """
    client = WebClient(token=token)
    cursor = None
    for _ in range(max_pages):
        response = client.conversations_list(types="public_channel,private_channel", exclude_archived=True, limit=200, cursor=cursor)
        for channel in response["channels"]:
            if channel.get("name") == name:
                return str(channel["id"])
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return None
    raise ChannelListIncompleteError(f"The first {max_pages} pages of channels don't hold {name!r} and there are more.")


def get_workspace_url(token: str) -> str:
    """Return the workspace's base URL (e.g. https://acme.slack.com/), the start of every message link."""
    return str(WebClient(token=token).auth_test()["url"])


def is_channel_member(channel: str, user_id: str, *, token: str, max_pages: int) -> bool | None:
    """Whether `user_id` is in the channel, reading at most `max_pages` pages of 1,000 members; None if not found and members are left unread."""
    client = WebClient(token=token)
    cursor = None
    for _ in range(max_pages):
        response = client.conversations_members(channel=channel, limit=1000, cursor=cursor)
        if user_id in response["members"]:
            return True
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return False
    return None


def list_files(channel: str, *, token: str) -> list[dict]:
    """List one file shared in the channel; only useful to learn whether the token has the files:read scope."""
    return list(WebClient(token=token).files_list(channel=channel, count=1)["files"])


def get_file_info(file_id: str, *, token: str) -> dict:
    """Return Slack's description of a file: name, filetype, size, the channels it is shared in and its private download URL (needs files:read)."""
    return dict(WebClient(token=token).files_info(file=file_id)["file"])


# httpx's timeout bounds each wait for the server, not the whole transfer: a response that trickles bytes would otherwise run on
# until the job timeout killed the agent run.
_DOWNLOAD_DEADLINE_SECONDS = 60.0


class FileDownloadError(Exception):
    """A file couldn't be downloaded; the message says why."""


def download_file(url: str, *, token: str, max_bytes: int) -> bytes:
    """Download a file Slack hosts, refusing anything bigger than `max_bytes`.

    Only https URLs on slack.com are fetched, and `url` must come from Slack's own file info, never from a user, because the
    bot's token is sent along.

    Raises:
        FileDownloadError: another host, an error status, an HTML page (what Slack returns when the token can't read files), too many
            bytes or a transfer that takes longer than `_DOWNLOAD_DEADLINE_SECONDS`.
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or not (parsed.host == "slack.com" or parsed.host.endswith(".slack.com")):
        raise FileDownloadError("The file is not hosted on Slack.")
    data = bytearray()
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_SECONDS
    try:
        with httpx.stream("GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=30.0, follow_redirects=False) as response:
            if response.status_code != 200:
                raise FileDownloadError(f"Slack answered {response.status_code} for the file.")
            if response.headers.get("content-type", "").startswith("text/html"):
                raise FileDownloadError("Slack returned a web page instead of the file; the app probably lacks the files:read scope.")
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise FileDownloadError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
                if time.monotonic() > deadline:
                    raise FileDownloadError(f"The download took longer than {_DOWNLOAD_DEADLINE_SECONDS:.0f} seconds.")
    except httpx.HTTPError as exc:
        raise FileDownloadError(f"Could not download the file: {exc}") from exc
    return bytes(data)


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
