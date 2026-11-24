import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace

from bots.core.models import BotConfig
from channel_analysis.core import extraction, limits
from channel_analysis.core.errors import ACCESS_MESSAGES, ChannelAccessError, ChannelAnalysisError
from channel_analysis.core.extraction_run import ChunkOutcome, Totals, count_outcomes, run_extraction
from channel_analysis.core.file_pages import cut_page, is_tabular, repeated_header
from channel_analysis.core.models import (
    AccessProblem,
    Analysis,
    ChannelAccess,
    ChannelInfo,
    ChannelMessage,
    ChannelSlice,
    ExtractionItem,
    ExtractionSpec,
    FilePage,
    FileRef,
)
from channel_analysis.core.ports import ChannelFiles, ChannelHistory, ExtractionModel
from channel_analysis.core.reference import parse_channel_reference

log = logging.getLogger("channel_analysis.service")

# What a result's `before` looks like: where to resume, and where the window started, so every page of one window has the same bounds.
_RESUME = re.compile(r"^(\d{9,11}\.\d{1,6})(?:@(\d{9,11}\.\d{1,6}))?$")
_DAY = 86400
# Paging one window takes minutes; a resume token whose window started longer ago than the reach plus this wasn't made by a read.
_RESUME_TOLERANCE_SECONDS = 3600


def _problem(code: str, blocking: bool = True) -> AccessProblem:
    return AccessProblem(code, ACCESS_MESSAGES[code], blocking)


def _problem_from(exc: ChannelAccessError) -> AccessProblem:
    return AccessProblem(exc.code, str(exc))


def _haystack(message: ChannelMessage) -> str:
    parts = [message.author, message.text, *(f.name for f in message.files)]
    for reply in message.replies:
        parts.extend((reply.author, reply.text, *(f.name for f in reply.files)))
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
    fetched: int = 0
    # Where a window the scan stopped early (scan cap) resumes; None when it read the whole window.
    next_before: str | None = None


@dataclass
class _FilesRead:
    """What an analysis call has done with files so far: how many it tried to read (a damaged file costs a download and a parse too),
    which it left out and why, and whether Slack refused them all."""

    tried: int = 0
    not_read: list[tuple[str, str]] = field(default_factory=list)
    blocked: str | None = None


@dataclass(frozen=True)
class _Collected:
    """What the scan of an analysis call gathered: the chunks to extract, and how far it got."""

    chunks: list[list[ExtractionItem]]
    files: _FilesRead
    messages_read: int
    messages_cut: int
    replies_omitted: int
    resume_at: str | None
    scanned: int


def _resume_token(cursor: str, window_start: float) -> str:
    return f"{cursor}@{window_start:.6f}"


class ChannelAnalysisService:
    """Reads a Slack channel for the agent: an access check, the messages of a window in pages, the files attached to them, and an
    analysis that has a model extract records from them and counts the records."""

    def __init__(self, history: ChannelHistory, files: ChannelFiles, extractor: ExtractionModel, now: Callable[[], float] = time.time) -> None:
        self._history = history
        self._files = files
        self._extractor = extractor
        self._now = now

    def check_access(self, bot: BotConfig, channel: str, requester_id: str) -> ChannelAccess:
        """Say whether the bot and the requester may read `channel`, listing every problem found rather than the first.

        `requester_id` is the admin the read is for: the person asking, or the owner of a scheduled question.

        Raises:
            ChannelAnalysisError: `channel` is empty or a direct message.
        """
        channel_id, info, problems = self._inspect(bot, channel, requester_id)
        empty_window = False
        # Probing reads a message, so it only runs when nothing blocks: a refused check can't tell the requester whether the channel has messages.
        if channel_id and not any(p.blocking for p in problems):
            empty_window = self._probe(bot, channel_id, problems)
        return ChannelAccess(channel_id, info.name if info else None, tuple(problems), empty_window)

    def read(
        self,
        bot: BotConfig,
        channel: str,
        requester_id: str,
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
        for raw, before_message in self._scan(bot, channel_id, (window_start, window_end), cursor, terms, include_replies, state):
            message = _clip(raw)
            if kept and chars + message.size > limits.PAGE_CHAR_BUDGET:
                resume_at = before_message
                break
            kept.append(message)
            chars += message.size
        else:
            resume_at = state.next_before
        next_before = _resume_token(resume_at, window_start) if resume_at else None
        return ChannelSlice(channel_id, window_start, window_end, terms, tuple(kept), state.scanned, next_before)

    def analyze(
        self,
        bot: BotConfig,
        channel: str,
        requester_id: str,
        spec: ExtractionSpec,
        *,
        group_by: tuple[str, ...] = (),
        include_files: bool = False,
        lookback_days: int = limits.DEFAULT_LOOKBACK_DAYS,
        end_days_ago: int = 0,
        contains: tuple[str, ...] = (),
        include_replies: bool = True,
        before: str | None = None,
    ) -> Analysis:
        """Have a model extract records from the messages of a window (and, with `include_files`, from the files attached to
        them or to their replies), then count the records by the `group_by` fields. At most `MAX_CHUNKS_PER_CALL` chunks are
        extracted: an unfinished window comes back with `next_before`, and the counts are for this call only. Window, filter and
        `before` are as in `read`.

        Raises:
            ChannelAnalysisError: a bad argument, or the bot or the requester may not read the channel.
        """
        started = time.monotonic()
        extraction.validate_spec(spec, group_by)
        terms = self._validate_window(lookback_days, end_days_ago, contains, before)
        channel_id = self._require_access(bot, channel, requester_id)
        window_start, window_end, cursor = self._window(lookback_days, end_days_ago, before)

        collected = self._collect(bot, channel_id, (window_start, window_end), cursor, terms, include_replies, include_files)
        outcomes = run_extraction(self._extractor, spec, collected.chunks)
        totals = count_outcomes(outcomes, group_by)
        self._log_analysis(channel_id, collected, outcomes, totals, time.monotonic() - started)
        return Analysis(
            channel_id=channel_id,
            window_start=window_start,
            window_end=window_end,
            terms=terms,
            fields=tuple(f.name for f in spec.fields),
            group_by=group_by,
            messages_read=collected.messages_read,
            messages_cut=collected.messages_cut,
            replies_omitted=collected.replies_omitted,
            scanned=collected.scanned,
            chunks=len(outcomes),
            failed_chunks=totals.failed,
            files_not_read=tuple(collected.files.not_read),
            records=totals.records,
            rejected=totals.rejected,
            groups=totals.groups,
            groups_omitted=totals.groups_omitted,
            records_omitted=totals.records_omitted,
            sample=totals.sample,
            next_before=_resume_token(collected.resume_at, window_start) if collected.resume_at else None,
        )

    def _collect(
        self, bot: BotConfig, channel_id: str, window: tuple[float, float], cursor: str, terms: tuple[str, ...], include_replies: bool, include_files: bool
    ) -> _Collected:
        """Scan the window and pack what it finds into chunks until the call's chunks are full or the window is done."""
        state = _ScanState()
        packer = extraction.ChunkPacker(limits.CHUNK_CHAR_BUDGET, limits.MAX_CHUNKS_PER_CALL)
        files = _FilesRead()
        messages_read = messages_cut = replies_omitted = 0
        resume_at: str | None = None
        scan_cap = limits.MAX_SCAN_PER_CALL_ANALYSIS_WITH_REPLIES if include_replies else limits.MAX_SCAN_PER_CALL_ANALYSIS
        for message, before_message in self._scan(bot, channel_id, window, cursor, terms, include_replies, state, scan_cap):
            # Files are read on a copy: a message that doesn't fit is read again by the next call, and must not count twice.
            trial = _FilesRead(files.tried, list(files.not_read), files.blocked)
            items, whole = self._items(bot, message, include_files, trial)
            if not packer.add(items):
                resume_at = before_message
                break
            files = trial
            messages_read += 1
            replies_omitted += message.omitted_replies
            if not whole:
                messages_cut += 1
        else:
            resume_at = state.next_before
        return _Collected(packer.chunks, files, messages_read, messages_cut, replies_omitted, resume_at, state.scanned)

    def _items(self, bot: BotConfig, message: ChannelMessage, include_files: bool, files: _FilesRead) -> tuple[list[ExtractionItem], bool]:
        """The items of one message, whole (not the display clip), and with `include_files` the readable files attached to it or to
        its replies. Also whether the message itself fit in the pieces one message may have. `files` is updated."""
        items, whole = extraction.message_items(message)
        if not include_files:
            return items, whole
        for source in (message, *message.replies):
            for ref in source.files:
                items.extend(self._file_pieces(bot, message, source, ref, len(items), files))
        return items, whole

    def _file_pieces(self, bot: BotConfig, message: ChannelMessage, source: ChannelMessage, ref: FileRef, items_so_far: int, files: _FilesRead) -> list[ExtractionItem]:
        """The pieces of one attached file as items of `message`; none, with the reason noted in `files`, when it can't be read."""
        reason = self._file_refusal(ref.size, items_so_far, files)
        text = None
        if reason is None:
            files.tried += 1
            try:
                text = self._files.text(ref.id, bot.bot_token)
            except ChannelAccessError as exc:
                files.blocked = reason = str(exc)
            except ChannelAnalysisError as exc:
                reason = str(exc)
        if text is None:
            files.not_read.append((ref.name, reason or "it has no text"))
            return []
        pieces, whole = extraction.file_items(message, ref, text, limits.MAX_ITEMS_PER_MESSAGE - items_so_far, source)
        if not whole:
            files.not_read.append((ref.name, "only the start of it was read: a message can add only so much"))
        return pieces

    @staticmethod
    def _file_refusal(size: int, items_so_far: int, files: _FilesRead) -> str | None:
        if files.blocked:
            return files.blocked
        if items_so_far >= limits.MAX_ITEMS_PER_MESSAGE:
            return "this message already has the most a message can add"
        if files.tried >= limits.MAX_FILES_PER_CALL:
            return f"a call reads at most {limits.MAX_FILES_PER_CALL} files"
        if size > limits.FILE_MAX_BYTES:
            return f"it is larger than {limits.FILE_MAX_BYTES // (1024 * 1024)} MB"
        return None

    @staticmethod
    def _log_analysis(channel_id: str, collected: _Collected, outcomes: list[ChunkOutcome], totals: Totals, seconds: float) -> None:
        log.info(
            "analyze channel=%s messages=%d cut=%d replies_omitted=%d scanned=%d chunks=%d failed=%d records=%d rejected=%d prompt_tokens=%d completion_tokens=%d "
            "chunk_seconds=%s total_seconds=%.1f",
            channel_id,
            collected.messages_read,
            collected.messages_cut,
            collected.replies_omitted,
            collected.scanned,
            len(outcomes),
            len(totals.failed),
            totals.records,
            totals.rejected,
            sum(o.prompt_tokens for o in outcomes),
            sum(o.completion_tokens for o in outcomes),
            [round(o.seconds, 1) for o in outcomes],
            seconds,
        )

    def read_file(self, bot: BotConfig, channel: str, requester_id: str, file_id: str, offset: int = 0) -> FilePage:
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
        page, next_offset = cut_page(text, offset, limits.FILE_PAGE_CHARS, repeated_header(text, offset, meta.filetype), is_tabular(meta.filetype))
        return FilePage(meta.name, meta.filetype, meta.size, page, offset, next_offset, len(text))

    def _window(self, lookback_days: int, end_days_ago: int, before: str | None) -> tuple[float, float, str]:
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
        cursor = f"{window_end:.6f}"
        if resumed and float(resumed.group(1)) < float(cursor):
            cursor = resumed.group(1)
        return window_start, window_end, cursor

    def _scan(
        self,
        bot: BotConfig,
        channel_id: str,
        window: tuple[float, float],
        cursor: str,
        terms: tuple[str, ...],
        include_replies: bool,
        state: _ScanState,
        scan_cap: int | None = None,
    ) -> Iterator[tuple[ChannelMessage, str | None]]:
        """Walk a window newest first and yield each matching message, whole, with the cursor from just before it.

        A consumer that stops at a message without using it resumes from that cursor. `state` counts the messages scanned (the
        unused one not included) and gets `next_before` when the scan stops at its cap with the window not finished. The cap
        counts the messages Slack returned, noise included, so a channel of joins can't keep a call fetching.
        """
        batch_size = limits.FETCH_BATCH_WITH_REPLIES if include_replies else limits.FETCH_BATCH
        scan_cap = scan_cap or (limits.MAX_SCAN_PER_CALL_WITH_REPLIES if include_replies else limits.MAX_SCAN_PER_CALL)
        while True:
            batch = self._history.fetch(channel_id, window[0], cursor, batch_size, include_replies, bot.bot_token, replies_until=window[1])
            state.fetched += batch.fetched
            for raw in batch.messages:
                haystack = _haystack(raw) if terms else ""
                if not terms or any(term in haystack for term in terms):
                    yield raw, cursor
                state.scanned += 1
                cursor = raw.ts
            if batch.last_ts is None or batch.exhausted:
                return
            cursor = batch.last_ts
            if state.fetched >= scan_cap:
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

    def _require_access(self, bot: BotConfig, channel: str, requester_id: str) -> str:
        """Run the checks that need no message content; refuse with the first blocking problem.

        Every read repeats them whether or not `check_access` ran first, so that a private channel can't be read by skipping it.
        """
        channel_id, _, problems = self._inspect(bot, channel, requester_id)
        blocking = next((problem for problem in problems if problem.blocking), None)
        if blocking or not channel_id:
            raise ChannelAnalysisError(blocking.message if blocking else ACCESS_MESSAGES["channel_not_found"])
        return channel_id

    def _inspect(self, bot: BotConfig, channel: str, requester_id: str) -> tuple[str | None, ChannelInfo | None, list[AccessProblem]]:
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
        elif info.is_private:
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
