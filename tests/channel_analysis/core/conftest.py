from dataclasses import dataclass, field

import pytest

from channel_analysis.core.errors import ChannelAccessError
from channel_analysis.core.models import ChannelInfo, ChannelMessage, FileMeta, HistoryBatch
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
        self.fetches: list[dict] = []
        self.member_checks: list[str] = []

    def find_channel_id(self, name: str, token: str) -> str | None:
        return self.names.get(name)

    def get_channel_info(self, channel_id: str, token: str) -> ChannelInfo:
        if self.info_error:
            raise self.info_error
        return self.infos[channel_id]

    def fetch(self, channel_id, oldest, latest, limit, with_replies, token) -> HistoryBatch:
        self.fetches.append({"oldest": oldest, "latest": latest, "limit": limit, "with_replies": with_replies})
        if self.fetch_error:
            raise self.fetch_error
        if self.script:
            return self.script.pop(0)
        eligible = [m for m in self.messages if float(m.ts) > oldest and (latest is None or float(m.ts) < float(latest))]
        batch = eligible[:limit]
        return HistoryBatch(batch, batch[-1].ts if batch else None, exhausted=len(eligible) <= limit)

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


@pytest.fixture
def history() -> FakeHistory:
    return FakeHistory()


@pytest.fixture
def files() -> FakeFiles:
    return FakeFiles()


@pytest.fixture
def service(history, files) -> ChannelAnalysisService:
    return ChannelAnalysisService(history, files, now=lambda: NOW)
