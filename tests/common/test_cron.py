from datetime import UTC, datetime, timedelta, timezone

import pytest

from common.cron import CronError, is_in_cron_window, validate_cron

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", ["0 * * * *", "0 2 * * *", "*/60 * * * *"])
def test_a_cron_at_or_above_the_minimum_interval_is_accepted(cron):
    validate_cron(cron, 60, "A dataset sync", NOW)


def test_a_cron_below_the_minimum_names_the_subject_and_the_minimum():
    with pytest.raises(CronError, match="A dataset sync can run at most every 60 minutes"):
        validate_cron("*/30 * * * *", 60, "A dataset sync", NOW)


def test_the_shortest_gap_across_midnight_counts():
    with pytest.raises(CronError, match="at most every 60"):
        validate_cron("0,40 0,2,23 * * *", 60, "A dataset sync", NOW)


@pytest.mark.parametrize("cron", ["", "* * * *", "61 * * * *", "0 0 31 2 *"])
def test_malformed_crons_are_rejected(cron):
    with pytest.raises(CronError):
        validate_cron(cron, 60, "A dataset sync", NOW)


DAYTIME = "*/30 8-17 * * *"


@pytest.mark.parametrize(
    "utc, inside",
    [
        (datetime(2026, 11, 4, 0, 59, tzinfo=UTC), False),  # 07:59 Vietnam
        (datetime(2026, 11, 4, 1, 0, tzinfo=UTC), True),  # 08:00 Vietnam, the first hour is included
        (datetime(2026, 11, 4, 5, 0, tzinfo=UTC), True),  # 12:00
        (datetime(2026, 11, 4, 10, 59, tzinfo=UTC), True),  # 17:59, the last minute of the last hour
        (datetime(2026, 11, 4, 11, 0, tzinfo=UTC), False),  # 18:00 Vietnam
        (datetime(2026, 11, 4, 20, 0, tzinfo=UTC), False),  # 03:00 the next day
    ],
    ids=["07:59", "08:00", "12:00", "17:59", "18:00", "03:00"],
)
def test_is_in_cron_window_reads_the_cron_in_its_own_timezone(utc, inside):
    assert is_in_cron_window(DAYTIME, "Asia/Ho_Chi_Minh", utc) is inside


def test_is_in_cron_window_does_not_read_a_local_cron_as_utc():
    late_in_vietnam = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)  # 23:00 Vietnam, 16:00 UTC

    assert is_in_cron_window(DAYTIME, "Asia/Ho_Chi_Minh", late_in_vietnam) is False
    assert is_in_cron_window(DAYTIME, "UTC", late_in_vietnam) is True


def test_is_in_cron_window_converts_from_any_timezone():
    noon_in_new_york = datetime(2026, 11, 4, 12, 0, tzinfo=timezone(timedelta(hours=-5)))  # 00:00 in Vietnam the next day

    assert is_in_cron_window(DAYTIME, "Asia/Ho_Chi_Minh", noon_in_new_york) is False


@pytest.mark.parametrize(
    "cron, inside",
    [
        ("*/30 8-17 * * 1-5", True),
        ("*/30 8-17 * * 6,0", False),
        ("0 9 * * *", False),
    ],
    ids=["weekdays", "weekends-only", "other-hour"],
)
def test_is_in_cron_window_honours_days_and_hours(cron, inside):
    noon_wednesday_in_vietnam = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)

    assert is_in_cron_window(cron, "Asia/Ho_Chi_Minh", noon_wednesday_in_vietnam) is inside


def test_is_in_cron_window_rejects_a_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_in_cron_window(DAYTIME, "UTC", datetime(2026, 11, 4, 12, 0))
