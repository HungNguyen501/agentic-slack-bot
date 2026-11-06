from unittest.mock import patch

import pytest
from slack_sdk.errors import SlackApiError

from agent.dataset_sync import service
from agent.dataset_sync.service import DatasetSyncError
from agent.dataset_sync.store import DatasetBusyError, DatasetNotSyncedError, DatasetSource
from connectors.databricks import DatabricksQueryError, RowSet
from connectors.db.bots import BotConfig
from connectors.db.schedules import Schedule

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def source(dataset="d", **overrides):
    fields = {
        "dataset": dataset, "description": "", "source_sql": "select 1", "enabled": True, "row_count": 0,
        "last_synced_at": None, "last_status": None, "last_error": None,
    } | overrides  # fmt: skip
    return DatasetSource(**fields)


def rowset(n):
    return RowSet(columns=["a"], rows=[{"a": str(i)} for i in range(n)])


@pytest.fixture
def store():
    with patch("agent.dataset_sync.service.store", autospec=True) as store:
        yield store


@pytest.fixture
def fetch():
    with patch("agent.dataset_sync.service.fetch_rows", autospec=True) as fetch:
        yield fetch


def test_a_successful_sync_replaces_the_whole_snapshot(store, fetch):
    store.get_source.return_value = source(row_count=3)
    fetch.return_value = rowset(4)

    result = service.sync_dataset("d")

    store.save_snapshot.assert_called_once_with("d", ["a"], rowset(4).rows)
    assert (result.row_count, result.previous_row_count) == (4, 3)


def test_an_unknown_or_disabled_dataset_is_refused_without_querying(store, fetch):
    store.get_source.return_value = None
    store.list_sources.return_value = [source("other")]
    with pytest.raises(DatasetSyncError, match="don't know a dataset named 'd'.*`other`"):
        service.sync_dataset("d")

    store.get_source.return_value = source(enabled=False)
    with pytest.raises(DatasetSyncError, match="disabled"):
        service.sync_dataset("d")

    fetch.assert_not_called()


def test_a_failed_query_keeps_the_old_snapshot_and_records_the_reason(store, fetch):
    store.get_source.return_value = source()
    fetch.side_effect = DatabricksQueryError("boom")

    with pytest.raises(DatasetSyncError, match="boom"):
        service.sync_dataset("d")

    store.save_snapshot.assert_not_called()
    store.record_failure.assert_called_once()
    assert "boom" in store.record_failure.call_args.args[1]


def test_a_dataset_deleted_mid_sync_is_not_reported_as_synced(store, fetch):
    store.get_source.return_value = source()
    fetch.return_value = rowset(2)
    store.save_snapshot.side_effect = DatasetNotSyncedError("Dataset 'd' no longer exists.")

    with pytest.raises(DatasetSyncError, match="no longer exists"):
        service.sync_dataset("d")


def test_an_empty_result_never_replaces_the_snapshot_even_when_forced(store, fetch):
    store.get_source.return_value = source(row_count=5)
    fetch.return_value = rowset(0)

    with pytest.raises(DatasetSyncError, match="no rows"):
        service.sync_dataset("d", force=True)

    store.save_snapshot.assert_not_called()


def test_a_sharp_shrink_is_refused_unless_forced(store, fetch):
    store.get_source.return_value = source(row_count=100)
    fetch.return_value = rowset(10)

    with pytest.raises(DatasetSyncError, match="returned 10 rows but the stored copy has 100"):
        service.sync_dataset("d")
    store.save_snapshot.assert_not_called()

    service.sync_dataset("d", force=True)
    store.save_snapshot.assert_called_once()


def test_a_small_dataset_may_shrink_freely(store, fetch):
    store.get_source.return_value = source(row_count=4)
    fetch.return_value = rowset(1)

    service.sync_dataset("d")

    store.save_snapshot.assert_called_once()


def test_the_whole_sync_runs_under_the_datasets_lock(store, fetch):
    store.get_source.return_value = source()
    fetch.return_value = rowset(2)
    calls = []
    store.dataset_lock.return_value.__enter__.side_effect = lambda: calls.append("lock")
    store.dataset_lock.return_value.__exit__.side_effect = lambda *args: calls.append("unlock")
    store.get_source.side_effect = lambda name: calls.append("read") or source()
    store.save_snapshot.side_effect = lambda *args: calls.append("save")

    service.sync_dataset("d")

    assert calls == ["lock", "read", "save", "unlock"]
    store.dataset_lock.assert_called_once_with("d")


def test_a_sync_is_refused_while_another_one_of_the_dataset_is_running(store, fetch):
    store.dataset_lock.return_value.__enter__.side_effect = DatasetBusyError("busy")

    with pytest.raises(service.DatasetSyncBusyError, match="already running"):
        service.sync_dataset("d")

    store.get_source.assert_not_called()
    fetch.assert_not_called()


def schedule(**overrides):
    fields = {"id": "s1", "cron": "0 2 * * *", "channel": "C1", "question": "label", "bot_id": "bot-a", "kind": "dataset_sync", "config": {"dataset": "d"}}
    return Schedule(**fields | overrides)


def test_a_scheduled_sync_is_silent_on_success():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule()),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True) as sync,
        patch("agent.dataset_sync.service.slack.post_message", autospec=True) as post,
    ):
        service.run_scheduled_sync(BOT, "s1")

    sync.assert_called_once_with("d")
    post.assert_not_called()


def test_a_failed_scheduled_sync_is_reported_in_the_schedules_channel():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule()),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True, side_effect=DatasetSyncError("nope")),
        patch("agent.dataset_sync.service.slack.post_message", autospec=True) as post,
    ):
        service.run_scheduled_sync(BOT, "s1")

    assert post.call_args.args[0] == "C1"
    assert "`d`" in post.call_args.args[1] and "nope" in post.call_args.args[1]
    assert post.call_args.kwargs == {"token": "xoxb-test"}


def test_a_scheduled_sync_that_finds_another_sync_running_stays_quiet():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule()),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True, side_effect=service.DatasetSyncBusyError("busy")),
        patch("agent.dataset_sync.service.slack.post_message", autospec=True) as post,
    ):
        service.run_scheduled_sync(BOT, "s1")

    post.assert_not_called()


def test_an_unexpected_error_is_reported_and_still_fails_the_job():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule()),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True, side_effect=RuntimeError("db down")),
        patch("agent.dataset_sync.service.slack.post_message", autospec=True) as post,
        pytest.raises(RuntimeError),
    ):
        service.run_scheduled_sync(BOT, "s1")

    assert post.call_args.args[0] == "C1"
    assert "RuntimeError" in post.call_args.args[1] and "unchanged" in post.call_args.args[1]


def test_a_failure_to_report_does_not_fail_the_job():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule()),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True, side_effect=DatasetSyncError("nope")),
        patch("agent.dataset_sync.service.slack.post_message", autospec=True, side_effect=SlackApiError("x", {"error": "channel_not_found"})),
    ):
        service.run_scheduled_sync(BOT, "s1")


@pytest.mark.parametrize("row", [None, schedule(status="expired"), schedule(kind="question")])
def test_a_missing_or_finished_schedule_does_nothing(row):
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=row),
        patch("agent.dataset_sync.service.sync_dataset", autospec=True) as sync,
    ):
        service.run_scheduled_sync(BOT, "s1")

    sync.assert_not_called()


def test_a_schedule_without_a_dataset_is_ended_instead_of_failing_every_tick():
    with (
        patch("agent.dataset_sync.service.get_schedule", autospec=True, return_value=schedule(config={})),
        patch("agent.dataset_sync.service.set_schedule_status", autospec=True) as end,
        patch("agent.dataset_sync.service.sync_dataset", autospec=True) as sync,
    ):
        service.run_scheduled_sync(BOT, "s1")

    end.assert_called_once_with("s1", "expired")
    sync.assert_not_called()
