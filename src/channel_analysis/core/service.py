import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

from bots.core.models import BotConfig
from channel_analysis.core import limits
from channel_analysis.core.errors import ACCESS_MESSAGES, ChannelAccessError, ChannelAnalysisError
from channel_analysis.core.file_pages import cut_page
from channel_analysis.core.models import AccessProblem, ChannelAccess, ChannelInfo, ChannelMessage, ChannelSlice, FilePage
from channel_analysis.core.ports import ChannelFiles, ChannelHistory
from channel_analysis.core.reference import parse_channel_reference

# What a result's `before` looks like: where to resume, and where the window started, so every page of one window has the same bounds.
_RESUME = re.compile(r"^(\d{9,11}\.\d{1,6})(?:@(\d{9,11}\.\d{1,6}))?$")
_DAY = 86400
# Paging one window takes minutes; a resume token whose window started longer ago than the reach plus this wasn't made by a read.
_RESUME_TOLERANCE_SECONDS = 3600
_NO_PROBE_CODES = frozenset({"channel_not_found", "is_dm", "not_in_channel", "private_not_member", "membership_unverified"})


def _problem(code: str, blocking: bool = True) -> AccessProblem:
    return AccessProblem(code, ACCESS_MESSAGES[code], blocking)


def _problem_from(exc: ChannelAccessError) -> AccessProblem:
    return AccessProblem(exc.code, str(exc))


def _haystack(message: ChannelMessage) -> str:
    parts = [message.author, message.text, *(f.name for f in message.files)]
    for reply in message.replies:
        parts.extend((reply.author, reply.text))
    return "\n".join(parts).lower()


def _clip(message: ChannelMessage) -> ChannelMessage:
    """Cut an oversized text, and drop the replies of a long thread past what one page may carry (they are counted)."""
    text = message.text
    if len(text) > limits.MESSAGE_CHAR_LIMIT:
        text = text[: limits.MESSAGE_CHAR_LIMIT] + " …[cut]"
    replies: list[ChannelMessage] = []
    used = 0
    for reply in message.replies:
        reply = _clip(reply)
        if used + reply.size > limits.REPLIES_CHAR_LIMIT and replies:
            break
        replies.append(reply)
        used += reply.size
    omitted = message.omitted_replies + len(message.replies) - len(replies)
    return replace(message, text=text, replies=tuple(replies), omitted_replies=omitted)


@dataclass
class _ScanState:
    """What a scan reports besides the messages it yields."""

    scanned: int = 0
    # Where a window the scan stopped early (scan cap) resumes; None when it read the whole window.
    next_before: str | None = None


def _resume_token(cursor: str, window_start: float) -> str:
    return f"{cursor}@{window_start:.6f}"


class ChannelAnalysisService:
    """Reads a Slack channel for the agent: an access check, the messages of a window in pages, and the files attached to them."""

    def __init__(self, history: ChannelHistory, files: ChannelFiles, now: Callable[[], float] = time.time) -> None:
        self._history = history
        self._files = files
        self._now = now

    def check_access(self, bot: BotConfig, channel: str, requester_id: str | None) -> ChannelAccess:
        """Say whether the bot and the requester may read `channel`, listing every problem found rather than the first.

        `requester_id` is None for a run with no requester (a scheduled question); the requester check is skipped then.

        Raises:
            ChannelAnalysisError: `channel` is empty or a direct message.
        """
        channel_id, info, problems = self._inspect(bot, channel, requester_id)
        empty_window = False
        # Nothing is read for a requester who may not see the channel, so a refused check can't tell them whether it has messages.
        if channel_id and not any(p.code in _NO_PROBE_CODES for p in problems):
            empty_window = self._probe(bot, channel_id, problems)
        return ChannelAccess(channel_id, info.name if info else None, tuple(problems), empty_window)

    def read(
        self,
        bot: BotConfig,
        channel: str,
        requester_id: str | None,
        *,
        lookback_days: int = limits.DEFAULT_LOOKBACK_DAYS,
        end_days_ago: int = 0,
        contains: tuple[str, ...] = (),
        include_replies: bool = True,
        before: str | None = None,
    ) -> ChannelSlice:
        """Read one page of the messages of a window, newest first.

        The window ends `end_days_ago` days ago and is `lookback_days` long; the filter keeps messages whose text, author, file
        names or replies contain any of `contains`. `before` resumes a window after the `next_before` of an earlier page.

        Raises:
            ChannelAnalysisError: a bad argument, or the bot or the requester may not read the channel.
        """
        terms = self._validate_window(lookback_days, end_days_ago, contains, before)
        channel_id = self._require_access(bot, channel, requester_id)
        window_start, window_end, cursor = self._window(lookback_days, end_days_ago, before)

        kept: list[ChannelMessage] = []
        chars = 0
        state = _ScanState()
        resume_at: str | None = None
        for message, before_message in self._scan(bot, channel_id, window_start, cursor, terms, include_replies, state):
            if kept and chars + message.size > limits.PAGE_CHAR_BUDGET:
                resume_at = before_message
                break
            kept.append(message)
            chars += message.size
        else:
            resume_at = state.next_before
        next_before = _resume_token(resume_at, window_start) if resume_at else None
        return ChannelSlice(channel_id, window_start, window_end, terms, tuple(kept), state.scanned, next_before)

    def read_file(self, bot: BotConfig, channel: str, requester_id: str | None, file_id: str, offset: int = 0) -> FilePage:
        """Read one page of the text of a file shared in `channel`.

        Raises:
            ChannelAnalysisError: a bad argument, a file not shared in the channel, or no access to the channel.
            FileUnreadableError: the file is too large or can't be turned into text.
        """
        if offset < 0:
            raise ChannelAnalysisError("offset can't be negative.")
        channel_id = self._require_access(bot, channel, requester_id)
        meta = self._files.describe(file_id, bot.bot_token)
        if channel_id not in meta.shared_in:
            raise ChannelAnalysisError("That file isn't shared in this channel, so I won't open it here.")
        if meta.size > limits.FILE_MAX_BYTES:
            raise ChannelAnalysisError(f"`{meta.name}` is {meta.size // 1024} KB; I only open files up to {limits.FILE_MAX_BYTES // (1024 * 1024)} MB.")
        text = self._files.text(file_id, bot.bot_token)
        header = text.split("\n", 1)[0] if meta.filetype.lower() in limits.TABULAR_FILE_TYPES else None
        page, next_offset = cut_page(text, offset, limits.FILE_PAGE_CHARS, header)
        return FilePage(meta.name, meta.filetype, meta.size, page, offset, next_offset, len(text))

    def _window(self, lookback_days: int, end_days_ago: int, before: str | None) -> tuple[float, float, str | None]:
        """The window's bounds and the cursor to read below. A `before` from an earlier result carries the window start, so
        the bounds stay the same from page to page; a bare timestamp starts a window from now.

        Raises:
            ChannelAnalysisError: the window start in `before` is further back than a window may reach, or in the future.
        """
        now = self._now()
        resumed = _RESUME.match(before) if before else None
        if resumed and resumed.group(2):
            window_start = float(resumed.group(2))
            window_end = window_start + lookback_days * _DAY
            reach = limits.MAX_REACH_DAYS * _DAY + _RESUME_TOLERANCE_SECONDS
            if not now - reach <= window_start <= now or window_end > now + _RESUME_TOLERANCE_SECONDS:
                raise ChannelAnalysisError("That `before` value doesn't belong to a window I can read. Start again without it.")
        else:
            window_end = now - end_days_ago * _DAY
            window_start = window_end - lookback_days * _DAY
        cursor = f"{window_end:.6f}" if end_days_ago else None
        if resumed and (cursor is None or float(resumed.group(1)) < float(cursor)):
            cursor = resumed.group(1)
        return window_start, window_end, cursor

    def _scan(
        self, bot: BotConfig, channel_id: str, window_start: float, cursor: str | None, terms: tuple[str, ...], include_replies: bool, state: _ScanState
    ) -> Iterator[tuple[ChannelMessage, str | None]]:
        """Walk a window newest first and yield each matching message (clipped) with the cursor from just before it.

        A consumer that stops at a message without using it resumes from that cursor. `state` counts the messages scanned (the
        unused one not included) and gets `next_before` when the scan stops at its cap with the window not finished.
        """
        batch_size = limits.FETCH_BATCH_WITH_REPLIES if include_replies else limits.FETCH_BATCH
        scan_cap = limits.MAX_SCAN_PER_CALL_WITH_REPLIES if include_replies else limits.MAX_SCAN_PER_CALL
        while True:
            batch = self._history.fetch(channel_id, window_start, cursor, batch_size, include_replies, bot.bot_token)
            for raw in batch.messages:
                haystack = _haystack(raw) if terms else ""
                if not terms or any(term in haystack for term in terms):
                    yield _clip(raw), cursor
                state.scanned += 1
                cursor = raw.ts
            if batch.last_ts is None or batch.exhausted:
                return
            cursor = batch.last_ts
            if state.scanned >= scan_cap:
                state.next_before = cursor
                return

    def _validate_window(self, lookback_days: int, end_days_ago: int, contains: tuple[str, ...], before: str | None) -> tuple[str, ...]:
        if lookback_days < 1 or end_days_ago < 0:
            raise ChannelAnalysisError("lookback_days must be at least 1 and end_days_ago at least 0.")
        if lookback_days + end_days_ago > limits.MAX_REACH_DAYS:
            raise ChannelAnalysisError(f"I read at most {limits.MAX_REACH_DAYS} days back: lookback_days + end_days_ago can't be more than that.")
        terms = tuple(dict.fromkeys(term.strip().lower() for term in contains if term and term.strip()))
        if len(terms) > limits.MAX_CONTAINS_TERMS:
            raise ChannelAnalysisError(f"`contains` takes at most {limits.MAX_CONTAINS_TERMS} terms.")
        if before is not None and not _RESUME.match(before):
            raise ChannelAnalysisError("`before` must be the value from a previous result, e.g. 1763900000.123456@1761308000.000000.")
        return terms

    def _require_access(self, bot: BotConfig, channel: str, requester_id: str | None) -> str:
        """Run the checks that need no message content; refuse with the first blocking problem.

        Every read repeats them whether or not `check_access` ran first, so that a private channel can't be read by skipping it.
        """
        channel_id, _, problems = self._inspect(bot, channel, requester_id)
        blocking = next((problem for problem in problems if problem.blocking), None)
        if blocking or not channel_id:
            raise ChannelAnalysisError(blocking.message if blocking else ACCESS_MESSAGES["channel_not_found"])
        return channel_id

    def _inspect(self, bot: BotConfig, channel: str, requester_id: str | None) -> tuple[str | None, ChannelInfo | None, list[AccessProblem]]:
        token = bot.bot_token
        reference = parse_channel_reference(channel)
        problems: list[AccessProblem] = []
        channel_id = reference.channel_id
        try:
            if channel_id is None and reference.name:
                channel_id = self._history.find_channel_id(reference.name, token)
            if channel_id is None:
                return None, None, [_problem("channel_not_found")]
            info = self._history.get_channel_info(channel_id, token)
        except ChannelAccessError as exc:
            return channel_id, None, [_problem_from(exc)]
        if info.is_dm:
            return channel_id, info, [_problem("is_dm")]
        if info.is_archived:
            problems.append(_problem("is_archived"))
        if not info.is_member:
            problems.append(_problem("not_in_channel"))
        elif info.is_private and requester_id is not None:
            problems.extend(self._requester_problems(channel_id, requester_id, token))
        return channel_id, info, problems

    def _requester_problems(self, channel_id: str, requester_id: str, token: str) -> list[AccessProblem]:
        try:
            member = self._history.is_member(channel_id, requester_id, token, limits.MEMBER_PAGES_MAX)
        except ChannelAccessError as exc:
            return [_problem_from(exc)]
        if member is None:
            return [_problem("membership_unverified")]
        return [] if member else [_problem("private_not_member")]

    def _probe(self, bot: BotConfig, channel_id: str, problems: list[AccessProblem]) -> bool:
        """Read one message to learn whether the token can read history at all, and one file listing for files. Returns True for an empty window."""
        window_end = self._now()
        empty = False
        try:
            batch = self._history.fetch(channel_id, window_end - limits.DEFAULT_LOOKBACK_DAYS * _DAY, None, 1, False, bot.bot_token)
            empty = not batch.messages and batch.last_ts is None
        except ChannelAccessError as exc:
            if not any(p.code == exc.code for p in problems):
                problems.append(_problem_from(exc))
        try:
            if not self._history.can_read_files(channel_id, bot.bot_token):
                problems.append(_problem("files_scope", blocking=False))
        except ChannelAccessError as exc:
            problems.append(AccessProblem(exc.code, str(exc), blocking=False))
        return empty
