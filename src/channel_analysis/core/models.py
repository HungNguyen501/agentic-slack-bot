from dataclasses import dataclass

from channel_analysis.core.limits import FILE_LINE_OVERHEAD_CHARS, MESSAGE_OVERHEAD_CHARS


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
        return len(self.author) + len(self.text) + files + MESSAGE_OVERHEAD_CHARS + sum(reply.size for reply in self.replies)


@dataclass(frozen=True)
class HistoryBatch:
    """One fetch of channel history, newest first. `last_ts` is the last message Slack returned, noise included, so a
    batch made only of noise still moves the cursor; `exhausted` means the window holds nothing older."""

    messages: list[ChannelMessage]
    last_ts: str | None
    exhausted: bool


@dataclass(frozen=True)
class ChannelInfo:
    name: str
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
    channel_name: str | None
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
