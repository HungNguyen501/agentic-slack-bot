"""The `synced_datasets` table: registry entries and the latest snapshot of each dataset."""
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from connectors.db.connection import connect
from dataset_sync.core.configs import DatasetSyncConfigs
from dataset_sync.core.errors import DatasetBusyError, DatasetNotSyncedError
from dataset_sync.core.models import DatasetSource


def _row_to_source(row: dict) -> DatasetSource:
    return DatasetSource(
        dataset=row["dataset"],
        description=row["description"],
        source_sql=row["source_sql"],
        enabled=row["enabled"],
        row_count=row["row_count"],
        last_synced_at=row["last_synced_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


class SupabaseDatasetRepository:
    @contextmanager
    def lock(self, dataset: str) -> Iterator[None]:
        """Hold a database lock on `dataset` for the whole block, so syncs of one dataset never overlap.

        Without it a slow sync could overwrite a newer snapshot, and the shrink guard would compare against a row count read
        before another sync changed it. The lock is transaction-scoped (taken on a connection whose transaction stays open
        for the block, which also works behind a transaction pooler) and non-blocking: a second sync is refused rather than
        queued, since it would only repeat the same query.

        Raises:
            DatasetBusyError: another sync of the dataset holds the lock.
        """
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS locked", (f"dataset_sync:{dataset}",))
            if not cur.fetchone()["locked"]:
                raise DatasetBusyError(f"A sync of dataset '{dataset}' is already running.")
            yield

    def list_sources(self) -> list[DatasetSource]:
        """All registered datasets, by name; the snapshots themselves are not loaded."""
        with connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {DatasetSyncConfigs.SOURCE_COLUMNS} FROM synced_datasets ORDER BY dataset")
            return [_row_to_source(row) for row in cur.fetchall()]

    def get_source(self, dataset: str) -> DatasetSource | None:
        """One registered dataset, or None if no such name is registered."""
        with connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {DatasetSyncConfigs.SOURCE_COLUMNS} FROM synced_datasets WHERE dataset = %s", (dataset,))
            row = cur.fetchone()
            return _row_to_source(row) if row else None

    def get_rows(self, dataset: str) -> list[dict[str, str | None]]:
        """The rows of a dataset's last successful sync, in the query's order; each row maps column name to value.

        This is what other features read. A failed later sync leaves the last good snapshot in place, so it keeps working.

        Raises:
            DatasetNotSyncedError: the dataset isn't registered, or no sync has succeeded yet.
        """
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT synced_rows, last_synced_at FROM synced_datasets WHERE dataset = %s", (dataset,))
            row = cur.fetchone()
        if row is None or row["last_synced_at"] is None:
            raise DatasetNotSyncedError(f"Dataset '{dataset}' has not been synced yet.")
        return row["synced_rows"]

    def save_snapshot(self, dataset: str, columns: list[str], rows: list[dict[str, str | None]]) -> None:
        """Replace a dataset's snapshot with a single UPDATE and mark the sync as successful.

        Raises:
            DatasetNotSyncedError: the dataset no longer exists, so nothing was saved.
        """
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE synced_datasets SET column_names = %s, synced_rows = %s, row_count = %s, last_synced_at = now(), last_status = 'ok', last_error = NULL "
                "WHERE dataset = %s",
                (columns, Jsonb(rows), len(rows), dataset),
            )
            if cur.rowcount == 0:
                raise DatasetNotSyncedError(f"Dataset '{dataset}' no longer exists.")

    def record_failure(self, dataset: str, error: str) -> None:
        """Note that a sync failed; the previous snapshot and its `last_synced_at` stay as they were."""
        with connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE synced_datasets SET last_status = 'error', last_error = %s WHERE dataset = %s", (error[:1000], dataset))
