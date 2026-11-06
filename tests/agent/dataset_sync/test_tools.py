from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from agent.dataset_sync import service, tools
from agent.dataset_sync.service import DatasetSyncError, SyncResult
from agent.dataset_sync.store import DatasetSource
from connectors.db.bots import BotConfig
from connectors.db.schedules import AddedDatasetSync, Schedule

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def source(dataset="store_locations", **overrides):
    fields = {
        "description": "Locations", "source_sql": "select 1", "enabled": True, "row_count": 250,
        "last_synced_at": datetime(2026, 11, 5, 2, 0, tzinfo=UTC), "last_status": "ok", "last_error": None,
    } | overrides  # fmt: skip
    return DatasetSource(dataset=dataset, **fields)


def existing_sync(cron="0 2 * * *"):
    return Schedule(id="s1", cron=cron, channel="C1", question="label", bot_id="bot-a", kind="dataset_sync", config={"dataset": "store_locations"})


@pytest.fixture
def store():
    with patch("agent.dataset_sync.service.store", autospec=True) as store, patch("agent.dataset_sync.tools.store", store):
        store.get_source.side_effect = lambda name: source(name) if name == "store_locations" else None
        store.list_sources.return_value = [source(), source("row_filter_tags", last_synced_at=None, last_status="error", last_error="denied")]
        yield store


def run(name, args, channel="C1"):
    return tools.dispatch_dataset_sync_tool(name, args, "UADMIN", channel, BOT)


def test_listing_shows_sync_state_failures_and_schedules(store):
    with patch("agent.dataset_sync.tools.get_dataset_sync_schedules", autospec=True, return_value={"store_locations": existing_sync()}):
        reply = run("list_synced_datasets", {})

    assert "last synced 2026-11-05 02:00 UTC, 250 rows" in reply
    assert "recurring sync `0 2 * * *` UTC (schedule id `s1`)" in reply
    assert "never synced; last attempt failed: denied; no recurring sync" in reply


def test_sync_runs_the_named_dataset_and_only_forces_on_an_explicit_true(store):
    with patch("agent.dataset_sync.tools.sync_dataset", autospec=True, return_value=SyncResult("store_locations", 260, 250)) as sync:
        reply = run("sync_dataset", {"dataset": "store_locations", "force": "yes"})

    sync.assert_called_once_with("store_locations", force=False)
    assert "260 rows (was 250)" in reply


def test_a_refused_sync_returns_the_reason_to_the_agent(store):
    with patch("agent.dataset_sync.tools.sync_dataset", autospec=True, side_effect=DatasetSyncError("too small")):
        assert run("sync_dataset", {"dataset": "store_locations"}) == "too small"


def test_an_unknown_dataset_lists_the_available_ones(store):
    with patch("agent.dataset_sync.tools.sync_dataset", autospec=True, wraps=service.sync_dataset):
        reply = run("sync_dataset", {"dataset": "nope"})

    assert "don't know a dataset named 'nope'" in reply and "`store_locations`" in reply and "`row_filter_tags`" in reply


def test_scheduling_creates_one_sync_reporting_to_the_current_channel(store):
    with patch("agent.dataset_sync.tools.add_dataset_sync", autospec=True, return_value=AddedDatasetSync(existing_sync(), created=True)) as add:
        reply = run("schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    _, cron, channel, _, bot_id, config = add.call_args.args
    assert (cron, channel, bot_id, config) == ("0 2 * * *", "C1", "bot-a", {"dataset": "store_locations", "created_by": "UADMIN"})
    assert "scheduled — id: `s1`" in reply


def test_scheduling_a_dataset_that_already_has_a_sync_returns_the_existing_one(store):
    with patch("agent.dataset_sync.tools.add_dataset_sync", autospec=True, return_value=AddedDatasetSync(existing_sync("0 3 * * *"), created=False)):
        reply = run("schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "already has a recurring sync" in reply and "`s1`" in reply and "You asked for `0 2 * * *`" in reply


def test_a_sync_owned_by_another_bot_is_not_offered_for_removal_here(store):
    other_bots_sync = existing_sync("0 3 * * *")
    other_bots_sync.bot_id = "bot-b"
    with patch("agent.dataset_sync.tools.add_dataset_sync", autospec=True, return_value=AddedDatasetSync(other_bots_sync, created=False)):
        reply = run("schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "another bot (`bot-b`)" in reply and "through that bot" in reply
    assert "ask me to remove" not in reply


def test_a_legacy_sync_with_no_owner_can_still_be_removed_from_any_bot(store):
    legacy = existing_sync("0 3 * * *")
    legacy.bot_id = None
    with patch("agent.dataset_sync.tools.add_dataset_sync", autospec=True, return_value=AddedDatasetSync(legacy, created=False)):
        reply = run("schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "ask me to remove schedule `s1`" in reply


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"dataset": "store_locations", "cron": "*/5 * * * *"}, "at most every 60 minutes"),
        ({"dataset": "store_locations", "cron": "nonsense"}, "exactly 5 fields"),
        ({"dataset": "store_locations", "cron": "0 2 * * *", "channel": "#general"}, "not a Slack channel ID"),
        ({"dataset": "nope", "cron": "0 2 * * *"}, "don't know a dataset"),
    ],
)
def test_scheduling_requests_that_cant_be_honoured_create_nothing(store, args, message):
    with patch("agent.dataset_sync.tools.add_dataset_sync", autospec=True) as add:
        assert message in run("schedule_dataset_sync", args)

    add.assert_not_called()
