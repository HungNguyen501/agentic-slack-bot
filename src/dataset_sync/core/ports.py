from contextlib import AbstractContextManager
from typing import Protocol

from dataset_sync.core.models import DatasetSource, FetchedRows


class DatasetReader(Protocol):
    """What other features need from a dataset: its last good snapshot."""

    def get_rows(self, dataset: str) -> list[dict[str, str | None]]:
        """The rows of a dataset's last successful sync, in the query's order; each row maps column name to value.

        Raises:
            DatasetNotSyncedError: the dataset isn't registered, or no sync has succeeded yet.
        """


class DatasetRepository(Protocol):
    def list_sources(self) -> list[DatasetSource]:
        """All registered datasets, by name; the snapshots themselves are not loaded."""

    def get_source(self, dataset: str) -> DatasetSource | None:
        """One registered dataset, or None if no such name is registered."""

    def save_snapshot(self, dataset: str, columns: list[str], rows: list[dict[str, str | None]]) -> None:
        """Replace a dataset's snapshot in one step and mark the sync as successful.

        Raises:
            DatasetNotSyncedError: the dataset no longer exists, so nothing was saved.
        """

    def record_failure(self, dataset: str, error: str) -> None:
        """Note that a sync failed; the previous snapshot and its `last_synced_at` stay as they were."""

    def lock(self, dataset: str) -> AbstractContextManager[None]:
        """Hold a non-blocking exclusive lock on `dataset` for the block.

        Raises:
            DatasetBusyError: another holder has it.
        """


class DatasetFetcher(Protocol):
    def fetch(self, sql: str, max_rows: int) -> FetchedRows:
        """Run a read-only query and return every row; errors rather than truncates past `max_rows`.

        Raises:
            DatasetFetchError: the query failed or exceeded `max_rows`.
        """


class FailureNotifier(Protocol):
    def post(self, channel: str, text: str, bot_token: str) -> None:
        """Post `text` to a Slack channel. A failed post is logged, not raised."""
