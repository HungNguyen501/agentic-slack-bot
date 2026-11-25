"""Sync a dataset from Databricks into Supabase, on demand or from a scheduled `dataset_sync` row."""
import logging
import uuid

from bots.core.models import BotConfig
from common.cron import CronError, validate_cron
from dataset_sync.core.configs import DatasetSyncConfigs
from dataset_sync.core.errors import DatasetBusyError, DatasetFetchError, DatasetNotSyncedError, DatasetSyncBusyError, DatasetSyncError
from dataset_sync.core.models import DatasetOverview, DatasetSource, SyncResult
from dataset_sync.core.ports import DatasetFetcher, DatasetRepository, FailureNotifier
from schedules.core.models import AddedDatasetSync
from schedules.core.ports import ScheduleRepository

log = logging.getLogger("dataset_sync.service")


class DatasetSyncService:
    def __init__(self, datasets: DatasetRepository, fetcher: DatasetFetcher, schedules: ScheduleRepository, notifier: FailureNotifier) -> None:
        self._datasets = datasets
        self._fetcher = fetcher
        self._schedules = schedules
        self._notifier = notifier

    def require_source(self, dataset: str) -> DatasetSource:
        """The registered, enabled dataset named `dataset`.

        Raises:
            DatasetSyncError: there is none, or it is disabled; an unknown name's message lists the registered ones.
        """
        source = self._datasets.get_source(dataset) if dataset else None
        if source is None:
            names = ", ".join(f"`{s.dataset}`" for s in self._datasets.list_sources()) or "none registered"
            raise DatasetSyncError(f"I don't know a dataset named '{dataset}'. Available datasets: {names}.")
        if not source.enabled:
            raise DatasetSyncError(f"Dataset `{dataset}` is disabled.")
        return source

    def list_datasets(self) -> list[DatasetOverview]:
        """Every registered dataset with its recurring sync, if any."""
        sources = self._datasets.list_sources()
        if not sources:
            return []
        recurring = self._schedules.get_dataset_sync_schedules()
        return [DatasetOverview(source, recurring.get(source.dataset)) for source in sources]

    def sync_dataset(self, dataset: str, *, force: bool = False) -> SyncResult:
        """Re-read a dataset from Databricks and replace its stored copy.

        The copy is replaced only by a complete, non-empty result. A failed sync leaves the previous copy in place and
        records the reason on the dataset. A result that would shrink a sizeable dataset sharply is refused unless `force`.
        Syncs of one dataset are serialized: the registry entry is read, the query run and the copy replaced under one lock,
        so a concurrent manual and scheduled sync can't overwrite each other or judge the shrink against stale numbers.

        Raises:
            DatasetSyncBusyError: another sync of this dataset is running.
            DatasetSyncError: the dataset is unknown or disabled, the query failed, or the result was refused.
        """
        try:
            with self._datasets.lock(dataset):
                return self._sync_locked(dataset, force)
        except DatasetBusyError as exc:
            raise DatasetSyncBusyError(f"A sync of `{dataset}` is already running; try again in a minute.") from exc

    def schedule_sync(self, dataset: str, cron: str, channel: str | None, current_channel: str, bot: BotConfig, user_id: str | None) -> AddedDatasetSync:
        """Create the recurring sync of `dataset`, or return the one that already exists (`created` False).

        `channel` is where failures are reported; it defaults to `current_channel`.

        Raises:
            DatasetSyncError: the dataset is unknown or disabled, the cron is malformed or too frequent, or `channel` isn't a channel ID.
        """
        source = self.require_source(dataset)
        try:
            validate_cron(cron, DatasetSyncConfigs.MIN_INTERVAL_MINUTES, "A dataset sync")
        except CronError as exc:
            raise DatasetSyncError(str(exc)) from exc
        report_channel = channel or current_channel
        if report_channel != current_channel and not DatasetSyncConfigs.CHANNEL_ID_RE.fullmatch(report_channel):
            raise DatasetSyncError(f"'{report_channel}' is not a Slack channel ID.")
        return self._schedules.add_dataset_sync(
            str(uuid.uuid4()), cron, report_channel, f"Dataset sync · {source.dataset}", bot.bot_id, {"dataset": source.dataset, "created_by": user_id}
        )

    def run_scheduled_sync(self, bot: BotConfig, schedule_id: str) -> None:
        """Run one tick of the `dataset_sync` schedule `schedule_id`. Silent on success; a failure is posted in the schedule's channel.

        A malformed row (no dataset in its config) is ended, like a malformed PR reminder, so it doesn't fail every tick.
        An unexpected error (say Supabase failing while saving) is reported too, then re-raised so the job shows as failed.
        """
        schedule = self._schedules.get_schedule(schedule_id)
        if schedule is None or schedule.kind != "dataset_sync" or schedule.status != "active":
            log.info("Dataset sync schedule %s is gone or no longer active; skipping", schedule_id)
            return
        dataset = schedule.config.get("dataset")
        if not isinstance(dataset, str) or not dataset:
            log.error("Dataset sync schedule %s has no dataset in its config; ending it", schedule_id)
            self._schedules.set_schedule_status(schedule_id, "expired")
            return

        try:
            self.sync_dataset(dataset)
        except DatasetSyncBusyError:
            log.info("Scheduled sync of %s skipped: another sync of it is running", dataset)
        except DatasetSyncError as exc:
            log.warning("Scheduled sync of %s failed: %s", dataset, exc)
            self._report_failure(bot, schedule.channel, dataset, str(exc))
        except Exception as exc:
            log.exception("Scheduled sync of %s failed unexpectedly", dataset)
            self._report_failure(bot, schedule.channel, dataset, f"unexpected error ({type(exc).__name__}); see the worker logs. The previous data is unchanged.")
            raise

    def _fail(self, dataset: str, reason: str) -> DatasetSyncError:
        self._datasets.record_failure(dataset, reason)
        return DatasetSyncError(reason)

    def _sync_locked(self, dataset: str, force: bool) -> SyncResult:
        source = self.require_source(dataset)

        try:
            result = self._fetcher.fetch(source.source_sql, DatasetSyncConfigs.MAX_ROWS)
        except DatasetFetchError as exc:
            raise self._fail(dataset, str(exc)) from exc

        if not result.rows:
            raise self._fail(dataset, "The query returned no rows, so the previous copy was kept.")

        shrunk_below = source.row_count * DatasetSyncConfigs.SHRINK_GUARD_RATIO
        if not force and source.row_count >= DatasetSyncConfigs.SHRINK_GUARD_MIN_ROWS and len(result.rows) < shrunk_below:
            raise self._fail(
                dataset,
                f"The query returned {len(result.rows)} rows but the stored copy has {source.row_count}; the previous copy was kept. "
                "Check the query and the privileges of the Databricks token, then sync with force if the drop is expected.",
            )

        try:
            self._datasets.save_snapshot(dataset, result.columns, result.rows)
        except DatasetNotSyncedError as exc:
            raise DatasetSyncError(str(exc)) from exc
        log.info("Synced dataset %s: %d row(s) (was %d)", dataset, len(result.rows), source.row_count)
        return SyncResult(dataset, len(result.rows), source.row_count)

    def _report_failure(self, bot: BotConfig, channel: str, dataset: str, reason: str) -> None:
        self._notifier.post(channel, f":warning: The scheduled sync of dataset `{dataset}` failed: {reason}", bot.bot_token)
