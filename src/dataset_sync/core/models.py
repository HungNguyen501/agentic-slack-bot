from dataclasses import dataclass
from datetime import datetime

from schedules.core.models import Schedule


@dataclass(frozen=True)
class DatasetSource:
    """A registered dataset: the query that produces it and the outcome of its last sync."""

    dataset: str
    description: str
    source_sql: str
    enabled: bool
    row_count: int
    last_synced_at: datetime | None
    last_status: str | None
    last_error: str | None


@dataclass(frozen=True)
class FetchedRows:
    columns: list[str]
    rows: list[dict[str, str | None]]


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    row_count: int
    previous_row_count: int


@dataclass(frozen=True)
class DatasetOverview:
    """A registered dataset with its recurring sync, if it has one."""

    source: DatasetSource
    schedule: Schedule | None
