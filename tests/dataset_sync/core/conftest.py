from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from bots.core.models import BotConfig
from dataset_sync.core.errors import DatasetBusyError, DatasetNotSyncedError
from dataset_sync.core.models import DatasetSource, FetchedRows
from dataset_sync.core.service import DatasetSyncService
from schedules.core.models import AddedDatasetSync, Schedule

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def _make_source(dataset="d", **overrides) -> DatasetSource:
    fields = {
        "description": "", "source_sql": "select 1", "enabled": True, "row_count": 0,
        "last_synced_at": datetime(2026, 11, 5, 2, 0, tzinfo=UTC), "last_status": "ok", "last_error": None,
    } | overrides  # fmt: skip
    return DatasetSource(dataset=dataset, **fields)


def _make_schedule(**overrides) -> Schedule:
    fields = {"id": "s1", "cron": "0 2 * * *", "channel": "C1", "question": "label", "bot_id": "bot-a", "kind": "dataset_sync", "config": {"dataset": "d"}}
    return Schedule(**fields | overrides)


def _make_rows(n) -> FetchedRows:
    return FetchedRows(columns=["a"], rows=[{"a": str(i)} for i in range(n)])


class FakeDatasets:
    def __init__(self) -> None:
        self.sources: dict[str, DatasetSource] = {}
        self.events: list[str] = []
        self.saved: list[tuple] = []
        self.failures: list[tuple[str, str]] = []
        self.busy = False
        self.deleted_mid_sync = False

    def list_sources(self) -> list[DatasetSource]:
        return sorted(self.sources.values(), key=lambda s: s.dataset)

    def get_source(self, dataset: str) -> DatasetSource | None:
        self.events.append("read")
        return self.sources.get(dataset)

    def save_snapshot(self, dataset: str, columns: list[str], rows: list[dict[str, str | None]]) -> None:
        self.events.append("save")
        if self.deleted_mid_sync:
            raise DatasetNotSyncedError(f"Dataset '{dataset}' no longer exists.")
        self.saved.append((dataset, columns, rows))

    def record_failure(self, dataset: str, error: str) -> None:
        self.failures.append((dataset, error))

    @contextmanager
    def lock(self, dataset: str) -> Iterator[None]:
        if self.busy:
            raise DatasetBusyError("busy")
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")


class FakeFetcher:
    def __init__(self) -> None:
        self.result: FetchedRows = _make_rows(1)
        self.error: Exception | None = None
        self.calls: list[tuple[str, int]] = []

    def fetch(self, sql: str, max_rows: int) -> FetchedRows:
        self.calls.append((sql, max_rows))
        if self.error:
            raise self.error
        return self.result


class FakeSchedules:
    def __init__(self) -> None:
        self.rows: dict[str, Schedule] = {}
        self.ended: list[tuple[str, str]] = []
        self.added: list[tuple] = []
        self.recurring: dict[str, Schedule] = {}
        self.existing: Schedule | None = None

    def get_schedule(self, id: str) -> Schedule | None:
        return self.rows.get(id)

    def set_schedule_status(self, id: str, status: str) -> None:
        self.ended.append((id, status))

    def get_dataset_sync_schedules(self) -> dict[str, Schedule]:
        return self.recurring

    def add_dataset_sync(self, id: str, cron: str, channel: str, label: str, bot_id: str, config: dict) -> AddedDatasetSync:
        if self.existing:
            return AddedDatasetSync(self.existing, created=False)
        self.added.append((cron, channel, label, bot_id, config))
        return AddedDatasetSync(_make_schedule(id=id, cron=cron, channel=channel, bot_id=bot_id, config=config), created=True)


@dataclass
class FakeNotifier:
    posts: list[tuple[str, str, str]] = field(default_factory=list)

    def post(self, channel: str, text: str, bot_token: str) -> None:
        self.posts.append((channel, text, bot_token))


@dataclass
class Rig:
    service: DatasetSyncService
    datasets: FakeDatasets
    fetcher: FakeFetcher
    schedules: FakeSchedules
    notifier: FakeNotifier


@pytest.fixture
def rig() -> Rig:
    datasets, fetcher, schedules, notifier = FakeDatasets(), FakeFetcher(), FakeSchedules(), FakeNotifier()
    datasets.sources["d"] = _make_source()
    return Rig(DatasetSyncService(datasets, fetcher, schedules, notifier), datasets, fetcher, schedules, notifier)


@pytest.fixture
def bot() -> BotConfig:
    return BOT


@pytest.fixture
def make_source():
    return _make_source


@pytest.fixture
def make_schedule():
    return _make_schedule


@pytest.fixture
def make_rows():
    return _make_rows
