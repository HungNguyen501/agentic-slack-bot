from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest

from bots.core.models import BotConfig
from dataset_sync.core.errors import DatasetSyncError
from dataset_sync.core.models import DatasetOverview, DatasetSource, SyncResult
from dataset_sync.core.service import DatasetSyncService
from dataset_sync.presentation import tools
from schedules.core.models import AddedDatasetSync, Schedule

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def source(dataset="store_locations", **overrides):
    fields = {
        "description": "Locations", "source_sql": "select 1", "enabled": True, "row_count": 250,
        "last_synced_at": datetime(2026, 11, 5, 2, 0, tzinfo=UTC), "last_status": "ok", "last_error": None,
    } | overrides  # fmt: skip
    return DatasetSource(dataset=dataset, **fields)


def existing_sync(cron="0 2 * * *", bot_id="bot-a"):
    return Schedule(id="s1", cron=cron, channel="C1", question="label", bot_id=bot_id, kind="dataset_sync", config={"dataset": "store_locations"})


@pytest.fixture
def service():
    return create_autospec(DatasetSyncService, instance=True)


def run(service, name, args, channel="C1"):
    return tools.dispatch_dataset_sync_tool(name, args, "UADMIN", channel, BOT, service)


def test_listing_shows_sync_state_failures_and_schedules(service):
    service.list_datasets.return_value = [
        DatasetOverview(source(), existing_sync()),
        DatasetOverview(source("row_filter_tags", last_synced_at=None, last_status="error", last_error="denied"), None),
    ]

    reply = run(service, "list_synced_datasets", {})

    assert "last synced 2026-11-05 02:00 UTC, 250 rows" in reply
    assert "recurring sync `0 2 * * *` UTC (schedule id `s1`)" in reply
    assert "never synced; last attempt failed: denied; no recurring sync" in reply


def test_listing_with_no_datasets_says_so(service):
    service.list_datasets.return_value = []

    assert run(service, "list_synced_datasets", {}) == "No datasets are registered."


def test_sync_runs_the_named_dataset_and_only_forces_on_an_explicit_true(service):
    service.sync_dataset.return_value = SyncResult("store_locations", 260, 250)

    reply = run(service, "sync_dataset", {"dataset": "store_locations", "force": "yes"})

    service.sync_dataset.assert_called_once_with("store_locations", force=False)
    assert "260 rows (was 250)" in reply


def test_a_refused_sync_returns_the_reason_to_the_agent(service):
    service.sync_dataset.side_effect = DatasetSyncError("too small")

    assert run(service, "sync_dataset", {"dataset": "store_locations"}) == "too small"


def test_an_unexpected_error_is_returned_as_text_not_raised(service):
    service.sync_dataset.side_effect = RuntimeError("db down")

    assert run(service, "sync_dataset", {"dataset": "store_locations"}) == "Error executing sync_dataset: db down"


def test_scheduling_passes_the_request_to_the_service_and_confirms(service):
    service.schedule_sync.return_value = AddedDatasetSync(existing_sync(), created=True)

    reply = run(service, "schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    service.schedule_sync.assert_called_once_with("store_locations", "0 2 * * *", None, "C1", BOT, "UADMIN")
    assert "scheduled — id: `s1`" in reply and "<#C1>" in reply


def test_scheduling_a_dataset_that_already_has_a_sync_returns_the_existing_one(service):
    service.schedule_sync.return_value = AddedDatasetSync(existing_sync("0 3 * * *"), created=False)

    reply = run(service, "schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "already has a recurring sync" in reply and "`s1`" in reply and "You asked for `0 2 * * *`" in reply


def test_a_sync_owned_by_another_bot_is_not_offered_for_removal_here(service):
    service.schedule_sync.return_value = AddedDatasetSync(existing_sync("0 3 * * *", bot_id="bot-b"), created=False)

    reply = run(service, "schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "another bot (`bot-b`)" in reply and "through that bot" in reply
    assert "ask me to remove" not in reply


def test_a_legacy_sync_with_no_owner_can_still_be_removed_from_any_bot(service):
    service.schedule_sync.return_value = AddedDatasetSync(existing_sync("0 3 * * *", bot_id=None), created=False)

    reply = run(service, "schedule_dataset_sync", {"dataset": "store_locations", "cron": "0 2 * * *"})

    assert "ask me to remove schedule `s1`" in reply


def test_a_scheduling_request_the_service_refuses_is_relayed(service):
    service.schedule_sync.side_effect = DatasetSyncError("A dataset sync can run at most every 60 minutes.")

    assert "at most every 60 minutes" in run(service, "schedule_dataset_sync", {"dataset": "store_locations", "cron": "*/5 * * * *"})


def test_an_unknown_tool_name_is_reported(service):
    assert run(service, "nope", {}) == "Unknown dataset sync tool: nope"
