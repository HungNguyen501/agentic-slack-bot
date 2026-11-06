"""Sync a dataset from Databricks into Supabase, on demand or from a scheduled `dataset_sync` row."""
import logging
from dataclasses import dataclass

from slack_sdk.errors import SlackApiError

from agent.dataset_sync import store
from agent.dataset_sync.store import DatasetBusyError, DatasetNotSyncedError, DatasetSource
from common.configs import AgentConfigs
from connectors import slack
from connectors.databricks import DatabricksQueryError, fetch_rows
from connectors.db.bots import BotConfig
from connectors.db.schedules import get_schedule, set_schedule_status

log = logging.getLogger("agent.dataset_sync.service")


class DatasetSyncError(Exception):
    """A sync didn't replace the stored dataset; the message is fit to show an admin and the previous copy is intact."""


class DatasetSyncBusyError(DatasetSyncError):
    """Another sync of the same dataset is running, so this one did nothing."""


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    row_count: int
    previous_row_count: int


def _fail(dataset: str, reason: str) -> DatasetSyncError:
    store.record_failure(dataset, reason)
    return DatasetSyncError(reason)


def require_source(dataset: str) -> DatasetSource:
    """The registered, enabled dataset named `dataset`.

    Raises:
        DatasetSyncError: there is none, or it is disabled; an unknown name's message lists the registered ones.
    """
    source = store.get_source(dataset) if dataset else None
    if source is None:
        names = ", ".join(f"`{s.dataset}`" for s in store.list_sources()) or "none registered"
        raise DatasetSyncError(f"I don't know a dataset named '{dataset}'. Available datasets: {names}.")
    if not source.enabled:
        raise DatasetSyncError(f"Dataset `{dataset}` is disabled.")
    return source


def sync_dataset(dataset: str, *, force: bool = False) -> SyncResult:
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
        with store.dataset_lock(dataset):
            return _sync_locked(dataset, force)
    except DatasetBusyError as exc:
        raise DatasetSyncBusyError(f"A sync of `{dataset}` is already running; try again in a minute.") from exc


def _sync_locked(dataset: str, force: bool) -> SyncResult:
    source = require_source(dataset)

    try:
        result = fetch_rows(source.source_sql, max_rows=AgentConfigs.DATASET_SYNC_MAX_ROWS)
    except DatabricksQueryError as exc:
        raise _fail(dataset, f"Databricks query failed: {exc}") from exc

    if not result.rows:
        raise _fail(dataset, "The query returned no rows, so the previous copy was kept.")

    shrunk_below = source.row_count * AgentConfigs.DATASET_SYNC_SHRINK_GUARD_RATIO
    if not force and source.row_count >= AgentConfigs.DATASET_SYNC_SHRINK_GUARD_MIN_ROWS and len(result.rows) < shrunk_below:
        raise _fail(
            dataset,
            f"The query returned {len(result.rows)} rows but the stored copy has {source.row_count}; the previous copy was kept. "
            "Check the query and the privileges of the Databricks token, then sync with force if the drop is expected.",
        )

    try:
        store.save_snapshot(dataset, result.columns, result.rows)
    except DatasetNotSyncedError as exc:
        raise DatasetSyncError(str(exc)) from exc
    log.info("Synced dataset %s: %d row(s) (was %d)", dataset, len(result.rows), source.row_count)
    return SyncResult(dataset, len(result.rows), source.row_count)


def _report_failure(bot: BotConfig, channel: str, dataset: str, reason: str) -> None:
    try:
        slack.post_message(channel, f":warning: The scheduled sync of dataset `{dataset}` failed: {reason}", token=bot.bot_token)
    except SlackApiError:
        log.exception("Could not report the failed sync of %s in channel %s", dataset, channel)


def run_scheduled_sync(bot: BotConfig, schedule_id: str) -> None:
    """Run one tick of the `dataset_sync` schedule `schedule_id`. Silent on success; a failure is posted in the schedule's channel.

    A malformed row (no dataset in its config) is ended, like a malformed PR reminder, so it doesn't fail every tick.
    An unexpected error (say Supabase failing while saving) is reported too, then re-raised so the job shows as failed.
    """
    schedule = get_schedule(schedule_id)
    if schedule is None or schedule.kind != "dataset_sync" or schedule.status != "active":
        log.info("Dataset sync schedule %s is gone or no longer active; skipping", schedule_id)
        return
    dataset = schedule.config.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        log.error("Dataset sync schedule %s has no dataset in its config; ending it", schedule_id)
        set_schedule_status(schedule_id, "expired")
        return

    try:
        sync_dataset(dataset)
    except DatasetSyncBusyError:
        log.info("Scheduled sync of %s skipped: another sync of it is running", dataset)
    except DatasetSyncError as exc:
        log.warning("Scheduled sync of %s failed: %s", dataset, exc)
        _report_failure(bot, schedule.channel, dataset, str(exc))
    except Exception as exc:
        log.exception("Scheduled sync of %s failed unexpectedly", dataset)
        _report_failure(bot, schedule.channel, dataset, f"unexpected error ({type(exc).__name__}); see the worker logs. The previous data is unchanged.")
        raise
