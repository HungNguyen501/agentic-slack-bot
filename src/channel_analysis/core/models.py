from dataclasses import dataclass

from channel_analysis.core.limits import FILE_LINE_OVERHEAD_CHARS, ITEM_MARKER_OVERHEAD_CHARS, MESSAGE_OVERHEAD_CHARS, REACTION_OVERHEAD_CHARS


@dataclass(frozen=True)
class FileRef:
    """A file attached to a message; its content is read only through `ChannelAnalysisService.read_file`."""

    id: str
    name: str
    filetype: str
    size: int


@dataclass(frozen=True)
class FileMeta:
    """What Slack says about a file, enough to decide whether it may be read."""

    name: str
    filetype: str
    size: int
    shared_in: frozenset[str]


@dataclass(frozen=True)
class ChannelMessage:
    ts: str
    author: str
    text: str
    link: str | None = None
    reactions: tuple[tuple[str, int], ...] = ()
    files: tuple[FileRef, ...] = ()
    reply_count: int = 0
    replies: tuple["ChannelMessage", ...] = ()
    omitted_replies: int = 0

    @property
    def size(self) -> int:
        """Characters this message takes in a page, replies included."""
        files = sum(len(f.name) + len(f.filetype) + FILE_LINE_OVERHEAD_CHARS for f in self.files)
        reactions = sum(len(name) + len(str(count)) + REACTION_OVERHEAD_CHARS for name, count in self.reactions)
        return len(self.author) + len(self.text) + files + reactions + MESSAGE_OVERHEAD_CHARS + sum(reply.size for reply in self.replies)


@dataclass(frozen=True)
class HistoryBatch:
    """One fetch of channel history, newest first. `last_ts` is the last message Slack returned, noise included, so a
    batch made only of noise still moves the cursor; `fetched` is how many that was; `exhausted` means the window holds nothing older."""

    messages: list[ChannelMessage]
    last_ts: str | None
    exhausted: bool
    fetched: int


@dataclass(frozen=True)
class ChannelInfo:
    is_private: bool
    is_archived: bool
    is_member: bool
    is_dm: bool


@dataclass(frozen=True)
class ChannelSlice:
    """What one `read` returned: the matching messages (newest first) of a window, and where to resume if the window isn't finished."""

    channel_id: str
    window_start: float
    window_end: float
    terms: tuple[str, ...]
    messages: tuple[ChannelMessage, ...]
    scanned: int
    next_before: str | None


@dataclass(frozen=True)
class AccessProblem:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class ChannelAccess:
    channel_id: str | None
    problems: tuple[AccessProblem, ...]
    empty_window: bool = False

    @property
    def ok(self) -> bool:
        return not any(problem.blocking for problem in self.problems)


@dataclass(frozen=True)
class FilePage:
    """One page of a file's text. `next_offset` is None on the last page; `text` may start with a repeated CSV header."""

    name: str
    filetype: str
    size: int
    text: str
    offset: int
    next_offset: int | None
    total_chars: int


@dataclass(frozen=True)
class ExtractionField:
    """One column of the records an analysis extracts. With `allowed_values`, a record whose value is not one of them is rejected."""

    name: str
    description: str
    allowed_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionSpec:
    instruction: str
    fields: tuple[ExtractionField, ...]


@dataclass(frozen=True)
class ExtractionItem:
    """One numbered piece of a chunk: a message, or a piece of a file attached to it. `ts` and `link` are the message's."""

    ts: str
    link: str | None
    heading: str
    text: str

    @property
    def size(self) -> int:
        return len(self.heading) + len(self.text) + ITEM_MARKER_OVERHEAD_CHARS


@dataclass(frozen=True)
class ModelAnswer:
    """What the extraction model returned for a chunk: the parsed JSON, and what it cost."""

    data: dict
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Record:
    ts: str
    link: str | None
    values: dict[str, str | None]


@dataclass(frozen=True)
class GroupCount:
    """What one combination of the counted-by fields came to: records, distinct source messages, distinct UTC dates."""

    key: tuple[str | None, ...]
    records: int
    messages: int
    dates: int
    links: tuple[str, ...]


@dataclass(frozen=True)
class FailedChunk:
    """A chunk that gave no records; its messages are not in the counts."""

    start_ts: str
    end_ts: str
    messages: int
    reason: str


@dataclass(frozen=True)
class Analysis:
    """What one `analyze` call found. Counts are for this call only; `next_before` resumes a window that is not finished."""

    channel_id: str
    window_start: float
    window_end: float
    terms: tuple[str, ...]
    fields: tuple[str, ...]
    group_by: tuple[str, ...]
    messages_read: int
    messages_cut: int
    replies_omitted: int
    scanned: int
    chunks: int
    failed_chunks: tuple[FailedChunk, ...]
    files_not_read: tuple[tuple[str, str], ...]
    records: int
    rejected: int
    groups: tuple[GroupCount, ...]
    groups_omitted: int
    records_omitted: int
    sample: tuple[Record, ...]
    next_before: str | None

    @property
    def unreliable(self) -> bool:
        """More than half the chunks failed: the counts leave out too much to be trusted."""
        return self.chunks > 0 and len(self.failed_chunks) * 2 > self.chunks
