from dataclasses import dataclass, field, replace

import pytest

from channel_analysis.core.errors import ChannelAccessError
from channel_analysis.core.models import ChannelInfo, ChannelMessage, FileMeta, HistoryBatch, ModelAnswer
from channel_analysis.core.service import ChannelAnalysisService
from tests.support.channel_analysis import CHANNEL, NOW, make_info


class FakeHistory:
    """In-memory ChannelHistory: `messages` newest first; set `script` to hand out batches in order instead."""

    def __init__(self) -> None:
        self.infos: dict[str, ChannelInfo] = {CHANNEL: make_info()}
        self.names: dict[str, str] = {"mail-reports": CHANNEL}
        self.messages: list[ChannelMessage] = []
        self.script: list[HistoryBatch] = []
        self.members: dict[str, bool | None] = {}
        self.files_readable = True
        self.info_error: ChannelAccessError | None = None
        self.fetch_error: ChannelAccessError | None = None
        self.members_error: ChannelAccessError | None = None
        self.files_error: ChannelAccessError | None = None
        self.find_error: ChannelAccessError | None = None
        self.fetches: list[dict] = []
        self.member_checks: list[str] = []
        self.threads: dict[str, ChannelMessage] = {}
        self.thread_reads: list[str] = []

    def find_channel_id(self, name: str, token: str) -> str | None:
        if self.find_error:
            raise self.find_error
        return self.names.get(name)

    def get_channel_info(self, channel_id: str, token: str) -> ChannelInfo:
        if self.info_error:
            raise self.info_error
        return self.infos[channel_id]

    def fetch(self, channel_id, oldest, latest, limit, with_replies, token, replies_until=None) -> HistoryBatch:
        self.fetches.append({"oldest": oldest, "latest": latest, "limit": limit, "with_replies": with_replies, "replies_until": replies_until})
        if self.fetch_error:
            raise self.fetch_error
        if self.script:
            return self.script.pop(0)
        eligible = [m for m in self.messages if float(m.ts) > oldest and (latest is None or float(m.ts) < float(latest))]
        batch = [self._as_read(m, with_replies, replies_until) for m in eligible[:limit]]
        return HistoryBatch(batch, batch[-1].ts if batch else None, exhausted=len(eligible) <= limit, fetched=len(batch))

    @staticmethod
    def _as_read(message: ChannelMessage, with_replies: bool, replies_until: float | None) -> ChannelMessage:
        """The message as the real adapter returns it: no replies unless asked for, and none posted after the window's end."""
        if not with_replies:
            return replace(message, replies=())
        if replies_until is None:
            return message
        kept = tuple(r for r in message.replies if float(r.ts) <= replies_until)
        return replace(message, replies=kept, reply_count=max(message.reply_count - (len(message.replies) - len(kept)), 0))

    def fetch_thread(self, channel_id: str, thread_ts: str, token: str) -> ChannelMessage | None:
        self.thread_reads.append(thread_ts)
        return self.threads.get(thread_ts)

    def is_member(self, channel_id: str, user_id: str, token: str, max_pages: int) -> bool | None:
        self.member_checks.append(user_id)
        if self.members_error:
            raise self.members_error
        return self.members.get(user_id, False)

    def can_read_files(self, channel_id: str, token: str) -> bool:
        if self.files_error:
            raise self.files_error
        return self.files_readable


@dataclass
class FakeFiles:
    metas: dict[str, FileMeta] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)

    def describe(self, file_id: str, token: str) -> FileMeta:
        return self.metas[file_id]

    def text(self, file_id: str, token: str) -> str:
        return self.texts[file_id]


class FakeExtractor:
    """In-memory ExtractionModel. `respond(system, user)` returns the JSON object to answer with (or an exception to raise);
    chunks run on several threads, so a test that has several chunks answers by what the chunk contains, not by call order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.respond = lambda system, user: {"records": []}
        self.tokens = (10, 5)

    def complete_json(self, system: str, user: str, timeout_seconds: float) -> ModelAnswer:
        self.calls.append((system, user))
        result = self.respond(system, user)
        if isinstance(result, Exception):
            raise result
        return ModelAnswer(result, *self.tokens)


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def history() -> FakeHistory:
    return FakeHistory()


@pytest.fixture
def files() -> FakeFiles:
    return FakeFiles()


@pytest.fixture
def service(history, files, extractor) -> ChannelAnalysisService:
    return ChannelAnalysisService(history, files, extractor, now=lambda: NOW)
