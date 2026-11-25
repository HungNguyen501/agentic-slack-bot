import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from slack_sdk.errors import SlackApiError

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import ChannelAccessError
from channel_analysis.core.models import ChannelInfo, ChannelMessage, HistoryBatch
from channel_analysis.infra import message_text
from connectors import slack

log = logging.getLogger("channel_analysis.slack_history")


def _error_code(exc: SlackApiError) -> str | None:
    return exc.response.get("error") if exc.response else None


def _retry_after(exc: SlackApiError) -> float | None:
    try:
        return float(exc.response.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


@contextmanager
def slack_access_errors(files: bool = False) -> Iterator[None]:
    """Turn the Slack errors that mean "this bot may not do that" into ChannelAccessError; anything else propagates.

    With `files`, a missing scope is the files scope. Slack names the scope it needed in `needed`.
    """
    try:
        yield
    except SlackApiError as exc:
        code = _error_code(exc)
        if code == "missing_scope":
            raise ChannelAccessError("files_scope" if files else "missing_scope", detail=exc.response.get("needed")) from exc
        if code in ChannelAnalysisConfigs.ACCESS_MESSAGES and code != "files_scope":
            raise ChannelAccessError(code, retry_after=_retry_after(exc)) from exc
        raise


@dataclass
class _ThreadReader:
    """Reads the threads of one fetch. Slack's limit is per minute, so once it still refuses after a wait, the other threads
    of the batch would be refused too: they are not asked, and the waiting is one budget for the batch, not one per thread."""

    waited: float = 0.0
    limited: bool = False

    def read(self, channel_id: str, ts: str, token: str) -> list[dict] | None:
        """A thread's messages (at most `REPLIES_FETCH_MAX`; one page of that size, but Slack may return fewer than asked and follow
        up with a cursor, so a thread can cost a few calls), or None when they can't be read: Slack refuses even after a wait, or says
        no for this thread. A page that is refused after others were read leaves the thread partly read (the rest counts as omitted).
        The page then shows the reply count instead of failing, since the channel itself was read."""
        if self.limited:
            return None
        retried = False

        def wait_and_retry(exc: SlackApiError) -> bool:
            nonlocal retried
            if _error_code(exc) != "ratelimited":
                return False
            wait = min(_retry_after(exc) or 1.0, ChannelAnalysisConfigs.MAX_RETRY_AFTER_SECONDS - self.waited)
            if retried or wait <= 0:
                self.limited = True
                log.warning("Slack is still rate limiting conversations.replies; the rest of this page is shown without replies")
                return False
            retried = True
            time.sleep(wait)
            self.waited += wait
            return True

        try:
            with slack_access_errors():
                return slack.conversations_replies(channel_id, ts, token=token, max_messages=ChannelAnalysisConfigs.REPLIES_FETCH_MAX, retry=wait_and_retry)
        except (SlackApiError, ChannelAccessError) as exc:
            code = exc.code if isinstance(exc, ChannelAccessError) else _error_code(exc)
            if code != "ratelimited":  # a rate limit that stopped the read was logged where it was given up on
                log.warning("Could not read the replies of thread %s of %s (%s); it is shown without them", ts, channel_id, code)
        return None


class SlackChannelHistory:
    def __init__(self) -> None:
        # One forked process serves one agent run, so these save an auth.test per page read, and the conversations.list
        # pages (up to 20 calls, against a limit of about 20 a minute) that finding a channel by name costs on every read.
        self._workspace_urls: dict[str, str] = {}
        self._channel_ids: dict[tuple[str, str], str] = {}

    def find_channel_id(self, name: str, token: str) -> str | None:
        if (token, name) not in self._channel_ids:
            try:
                with slack_access_errors():
                    channel_id = slack.find_channel_id(name, token=token)
            except slack.ChannelListIncompleteError as exc:
                raise ChannelAccessError("channel_search_incomplete") from exc
            if channel_id is None:
                return None
            self._channel_ids[(token, name)] = channel_id
        return self._channel_ids[(token, name)]

    def get_channel_info(self, channel_id: str, token: str) -> ChannelInfo:
        with slack_access_errors():
            raw = slack.get_channel_info(channel_id, token=token)
        return ChannelInfo(
            is_private=bool(raw.get("is_private") or raw.get("is_group")),
            is_archived=bool(raw.get("is_archived")),
            is_member=bool(raw.get("is_member")),
            is_dm=bool(raw.get("is_im") or raw.get("is_mpim")),
        )

    def fetch(
        self, channel_id: str, oldest: float, latest: str | None, limit: int, with_replies: bool, token: str, replies_until: float | None = None
    ) -> HistoryBatch:
        with slack_access_errors():
            raw_messages, truncated = slack.conversations_history(channel_id, oldest, token=token, max_messages=limit, latest=latest)
            workspace_url = self._workspace_url(token)
        threads = _ThreadReader() if with_replies else None
        # Built as the caller reads them: a thread is fetched when its message is reached, so a page that fills early doesn't pay
        # for (or lose to the rate limit) the threads of the messages it never gets to.
        messages = (
            self._to_message(raw, channel_id, workspace_url, token, threads, replies_until) for raw in raw_messages if not message_text.is_noise(raw)
        )
        return HistoryBatch(messages, raw_messages[-1]["ts"] if raw_messages else None, exhausted=not truncated, fetched=len(raw_messages))

    def is_member(self, channel_id: str, user_id: str, token: str, max_pages: int) -> bool | None:
        with slack_access_errors():
            return slack.is_channel_member(channel_id, user_id, token=token, max_pages=max_pages)

    def can_read_files(self, channel_id: str, token: str) -> bool:
        try:
            with slack_access_errors(files=True):
                slack.list_files(channel_id, token=token)
        except ChannelAccessError as exc:
            if exc.code == "files_scope":
                return False
            raise
        return True

    def _workspace_url(self, token: str) -> str:
        if token not in self._workspace_urls:
            self._workspace_urls[token] = slack.get_workspace_url(token).rstrip("/")
        return self._workspace_urls[token]

    def _to_message(
        self, raw: dict, channel_id: str, workspace_url: str, token: str, threads: _ThreadReader | None, replies_until: float | None
    ) -> ChannelMessage:
        replies: tuple[ChannelMessage, ...] = ()
        omitted = 0
        reply_count = int(raw.get("reply_count") or 0)
        if threads and reply_count and raw.get("thread_ts") == raw["ts"]:
            replies, omitted = self._thread(raw, channel_id, token, threads, replies_until, reply_count)
            reply_count = len(replies) + omitted
        link = f"{workspace_url}/archives/{channel_id}/p{raw['ts'].replace('.', '')}"
        return self._normalize(raw, link=link, reply_count=reply_count, replies=replies, omitted_replies=omitted)

    def _thread(
        self, raw: dict, channel_id: str, token: str, threads: _ThreadReader, replies_until: float | None, reply_count: int
    ) -> tuple[tuple[ChannelMessage, ...], int]:
        """The replies of a thread that belong to the window, and how many more Slack says exist that were not read."""
        thread = threads.read(channel_id, raw["ts"], token)
        if thread is None:
            return (), reply_count
        fetched = [r for r in thread if r["ts"] != raw["ts"]]
        # A reply posted after the window's end isn't part of the window, however late it came.
        in_window = [r for r in fetched if replies_until is None or float(r["ts"]) <= replies_until]
        # A broadcast reply is also a message of the channel, counted there; nested here it would be counted twice.
        shown = [r for r in in_window if not message_text.is_noise(r) and not message_text.is_broadcast(r)]
        # Replies come oldest first: once the ones read reach past the window's end, the unread rest is past it too.
        past_the_window = replies_until is not None and len(in_window) < len(fetched)
        omitted = 0 if past_the_window else max(reply_count - len(fetched), 0)
        return tuple(self._normalize(r) for r in shown), omitted

    @staticmethod
    def _normalize(raw: dict, **extra: object) -> ChannelMessage:
        """A message or a reply as the core type; `extra` are what only a root has (link, reply count, replies)."""
        return ChannelMessage(
            ts=raw["ts"],
            author=message_text.author_of(raw),
            text=message_text.flatten_text(raw),
            reactions=message_text.reactions_of(raw),
            files=message_text.files_of(raw),
            **extra,
        )
