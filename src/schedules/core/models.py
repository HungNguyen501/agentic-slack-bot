"""Rows of the schedules table as the rest of the app sees them."""
from dataclasses import dataclass, field
from typing import NamedTuple


@dataclass
class Schedule:
    id: str
    cron: str
    channel: str
    question: str
    bot_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    kind: str = "question"
    status: str = "active"
    config: dict = field(default_factory=dict)
    timezone: str = "UTC"


class AddedReminder(NamedTuple):
    """The reminder `add_pr_reminder` returns: the new row, or the equivalent one that already existed (`created` False)."""

    schedule: Schedule
    created: bool


class AddedDatasetSync(NamedTuple):
    """The schedule `add_dataset_sync` returns: the new row, or the one that already syncs that dataset (`created` False)."""

    schedule: Schedule
    created: bool
