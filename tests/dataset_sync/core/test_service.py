import pytest

from dataset_sync.core.errors import DatasetFetchError, DatasetSyncBusyError, DatasetSyncError


def test_a_successful_sync_replaces_the_whole_snapshot(rig, make_source, make_rows):
    rig.datasets.sources["d"] = make_source(row_count=3)
    rig.fetcher.result = make_rows(4)

    result = rig.service.sync_dataset("d")

    assert rig.datasets.saved == [("d", ["a"], make_rows(4).rows)]
    assert (result.row_count, result.previous_row_count) == (4, 3)


def test_the_source_query_runs_with_the_row_limit(rig):
    rig.service.sync_dataset("d")

    assert rig.fetcher.calls == [("select 1", 50_000)]


def test_an_unknown_or_disabled_dataset_is_refused_without_querying(rig, make_source):
    rig.datasets.sources["other"] = make_source("other")
    with pytest.raises(DatasetSyncError, match="don't know a dataset named 'x'.*`d`.*`other`"):
        rig.service.sync_dataset("x")

    rig.datasets.sources["d"] = make_source(enabled=False)
    with pytest.raises(DatasetSyncError, match="disabled"):
        rig.service.sync_dataset("d")

    assert rig.fetcher.calls == []


def test_a_failed_query_keeps_the_old_snapshot_and_records_the_reason(rig):
    rig.fetcher.error = DatasetFetchError("boom")

    with pytest.raises(DatasetSyncError, match="boom"):
        rig.service.sync_dataset("d")

    assert rig.datasets.saved == []
    assert len(rig.datasets.failures) == 1 and "boom" in rig.datasets.failures[0][1]


def test_a_dataset_deleted_mid_sync_is_not_reported_as_synced(rig, make_rows):
    rig.fetcher.result = make_rows(2)
    rig.datasets.deleted_mid_sync = True

    with pytest.raises(DatasetSyncError, match="no longer exists"):
        rig.service.sync_dataset("d")


def test_an_empty_result_never_replaces_the_snapshot_even_when_forced(rig, make_source, make_rows):
    rig.datasets.sources["d"] = make_source(row_count=5)
    rig.fetcher.result = make_rows(0)

    with pytest.raises(DatasetSyncError, match="no rows"):
        rig.service.sync_dataset("d", force=True)

    assert rig.datasets.saved == []


def test_a_sharp_shrink_is_refused_unless_forced(rig, make_source, make_rows):
    rig.datasets.sources["d"] = make_source(row_count=100)
    rig.fetcher.result = make_rows(10)

    with pytest.raises(DatasetSyncError, match="returned 10 rows but the stored copy has 100"):
        rig.service.sync_dataset("d")
    assert rig.datasets.saved == []

    rig.service.sync_dataset("d", force=True)
    assert len(rig.datasets.saved) == 1


def test_a_small_dataset_may_shrink_freely(rig, make_source, make_rows):
    rig.datasets.sources["d"] = make_source(row_count=4)
    rig.fetcher.result = make_rows(1)

    rig.service.sync_dataset("d")

    assert len(rig.datasets.saved) == 1


def test_the_whole_sync_runs_under_the_datasets_lock(rig):
    rig.service.sync_dataset("d")

    assert rig.datasets.events == ["lock", "read", "save", "unlock"]


def test_a_sync_is_refused_while_another_one_of_the_dataset_is_running(rig):
    rig.datasets.busy = True

    with pytest.raises(DatasetSyncBusyError, match="already running"):
        rig.service.sync_dataset("d")

    assert rig.datasets.events == []
    assert rig.fetcher.calls == []


def test_a_scheduled_sync_is_silent_on_success(rig, bot, make_schedule):
    rig.schedules.rows["s1"] = make_schedule()

    rig.service.run_scheduled_sync(bot, "s1")

    assert len(rig.datasets.saved) == 1
    assert rig.notifier.posts == []


def test_a_failed_scheduled_sync_is_reported_in_the_schedules_channel(rig, bot, make_schedule):
    rig.schedules.rows["s1"] = make_schedule()
    rig.fetcher.error = DatasetFetchError("nope")

    rig.service.run_scheduled_sync(bot, "s1")

    [(channel, text, token)] = rig.notifier.posts
    assert channel == "C1" and token == "xoxb-test"
    assert "`d`" in text and "nope" in text


def test_a_scheduled_sync_that_finds_another_sync_running_stays_quiet(rig, bot, make_schedule):
    rig.schedules.rows["s1"] = make_schedule()
    rig.datasets.busy = True

    rig.service.run_scheduled_sync(bot, "s1")

    assert rig.notifier.posts == []


def test_an_unexpected_error_is_reported_and_still_fails_the_job(rig, bot, make_schedule):
    rig.schedules.rows["s1"] = make_schedule()
    rig.fetcher.error = RuntimeError("db down")

    with pytest.raises(RuntimeError):
        rig.service.run_scheduled_sync(bot, "s1")

    [(channel, text, _)] = rig.notifier.posts
    assert channel == "C1" and "RuntimeError" in text and "unchanged" in text


@pytest.mark.parametrize("overrides", [{"status": "expired"}, {"kind": "question"}, None])
def test_a_missing_or_finished_schedule_does_nothing(rig, bot, make_schedule, overrides):
    if overrides is not None:
        rig.schedules.rows["s1"] = make_schedule(**overrides)

    rig.service.run_scheduled_sync(bot, "s1")

    assert rig.fetcher.calls == []


def test_a_schedule_without_a_dataset_is_ended_instead_of_failing_every_tick(rig, bot, make_schedule):
    rig.schedules.rows["s1"] = make_schedule(config={})

    rig.service.run_scheduled_sync(bot, "s1")

    assert rig.schedules.ended == [("s1", "expired")]
    assert rig.fetcher.calls == []


def test_scheduling_creates_one_sync_reporting_to_the_current_channel(rig, bot):
    added = rig.service.schedule_sync("d", "0 2 * * *", None, "C1", bot, "UADMIN")

    assert added.created
    assert rig.schedules.added == [("0 2 * * *", "C1", "Dataset sync · d", "bot-a", {"dataset": "d", "created_by": "UADMIN"})]


def test_scheduling_may_report_failures_to_another_channel_given_by_id(rig, bot):
    rig.service.schedule_sync("d", "0 2 * * *", "C0ABCDEF", "C1", bot, "UADMIN")

    assert rig.schedules.added[0][1] == "C0ABCDEF"


def test_scheduling_a_dataset_that_already_has_a_sync_returns_the_existing_one(rig, bot, make_schedule):
    rig.schedules.existing = make_schedule(cron="0 3 * * *")

    added = rig.service.schedule_sync("d", "0 2 * * *", None, "C1", bot, "UADMIN")

    assert not added.created and added.schedule.cron == "0 3 * * *"
    assert rig.schedules.added == []


@pytest.mark.parametrize(
    ("dataset", "cron", "channel", "message"),
    [
        ("d", "*/5 * * * *", None, "at most every 60 minutes"),
        ("d", "nonsense", None, "exactly 5 fields"),
        ("d", "0 2 * * *", "#general", "not a Slack channel ID"),
        ("nope", "0 2 * * *", None, "don't know a dataset"),
    ],
)
def test_scheduling_requests_that_cant_be_honoured_create_nothing(rig, bot, dataset, cron, channel, message):
    with pytest.raises(DatasetSyncError, match=message):
        rig.service.schedule_sync(dataset, cron, channel, "C1", bot, "UADMIN")

    assert rig.schedules.added == []


def test_listing_pairs_each_dataset_with_its_recurring_sync(rig, make_source, make_schedule):
    rig.datasets.sources["e"] = make_source("e")
    rig.schedules.recurring = {"d": make_schedule()}

    overviews = rig.service.list_datasets()

    assert [(o.source.dataset, o.schedule is not None) for o in overviews] == [("d", True), ("e", False)]
