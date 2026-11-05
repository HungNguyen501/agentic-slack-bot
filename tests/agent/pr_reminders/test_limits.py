from datetime import UTC, datetime, timedelta, timezone

import pytest

from agent.pr_reminders.limits import ReminderRequestError, is_reminder_expired, is_within_active_hours, parse_mentions, validate_reminder_cron
from common.configs import AgentConfigs

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "cron",
    ["*/30 * * * *", "0 * * * *", "0 9 * * 1-5", "0,30 * * * *", "15 */2 * * *", "0 0 29 2 *", "0,40 0,23 * * 1"],
    ids=["every-30m", "hourly", "weekdays-at-9", "half-hours", "odd-minute-every-2h", "leap-day-only", "short-wrap-but-only-mondays"],
)
def test_validate_reminder_cron_accepts_expressions_at_or_above_the_minimum_interval(cron):
    validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize(
    "cron",
    [
        "* * * * *",
        "*/5 * * * *",
        "*/15 * * * *",
        "*/29 * * * *",
        "0,29 * * * *",
        "0,10,40 * * * *",
        "*/20 9-18 * * 1-5",
        "0,40 0,2,4,6,8,10,12,14,16,18,20,23 * * *",
        "10,50 0,23 * * *",
    ],
    ids=["every-minute", "every-5m", "every-15m", "every-29m", "29m-gap", "irregular", "business-hours-20m", "23:40-to-00:00-wrap", "23:50-to-00:10-wrap"],
)
def test_validate_reminder_cron_rejects_expressions_that_fire_more_often_than_every_30_minutes(cron):
    with pytest.raises(ReminderRequestError, match="at most every 30 minutes"):
        validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize(
    "cron",
    ["", "*/15 * * *", "0 0 * * * *", "not a cron at all", "99 * * * *", "0 0 30 2 *"],
    ids=["empty", "4-fields", "6-fields", "garbage", "out-of-range", "date-that-never-exists"],
)
def test_validate_reminder_cron_rejects_malformed_expressions(cron):
    with pytest.raises(ReminderRequestError):
        validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize(
    "expires_at, expired",
    [
        (NOW.timestamp() + 60, False),
        (NOW.timestamp(), True),
        (NOW.timestamp() - 60, True),
    ],
    ids=["not-yet", "exactly-now", "past"],
)
def test_is_reminder_expired_compares_the_time_limit_with_now(expires_at, expired):
    assert is_reminder_expired(expires_at, NOW) is expired


def test_a_reminder_without_a_time_limit_never_expires():
    assert is_reminder_expired(None, NOW) is False


@pytest.mark.parametrize(
    "utc, allowed",
    [
        (datetime(2026, 11, 3, 23, 59, tzinfo=UTC), False),  # 06:59 Vietnam
        (datetime(2026, 11, 4, 0, 0, tzinfo=UTC), True),  # 07:00 Vietnam, start is inclusive
        (datetime(2026, 11, 4, 5, 0, tzinfo=UTC), True),  # 12:00
        (datetime(2026, 11, 4, 14, 59, tzinfo=UTC), True),  # 21:59
        (datetime(2026, 11, 4, 15, 0, tzinfo=UTC), False),  # 22:00 Vietnam, end is exclusive
        (datetime(2026, 11, 4, 20, 0, tzinfo=UTC), False),  # 03:00 the next day
    ],
    ids=["06:59", "07:00", "12:00", "21:59", "22:00", "03:00"],
)
def test_is_within_active_hours_uses_vietnam_time(utc, allowed):
    assert is_within_active_hours(utc) is allowed


def test_is_within_active_hours_converts_from_any_timezone():
    noon_in_new_york = datetime(2026, 11, 4, 12, 0, tzinfo=timezone(timedelta(hours=-5)))  # 00:00 in Vietnam the next day

    assert is_within_active_hours(noon_in_new_york) is False


def test_is_within_active_hours_rejects_a_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_within_active_hours(datetime(2026, 11, 4, 12, 0))


@pytest.mark.parametrize(
    "values, expected",
    [
        (["<@U123ABCDE>"], ["U123ABCDE"]),
        (["U123ABCDE"], ["U123ABCDE"]),
        (["<@U123ABCDE|alice>"], ["U123ABCDE"]),
        ([" <@W9XYZ12345> "], ["W9XYZ12345"]),
        (["<@U01ABCDEF>", "U02GHIJKL", "<@U01ABCDEF>"], ["U01ABCDEF", "U02GHIJKL"]),
        ([], []),
    ],
    ids=["mention", "bare-id", "mention-with-name", "enterprise-id-and-spaces", "duplicates-dropped-in-order", "none"],
)
def test_parse_mentions_returns_distinct_user_ids_in_order(values, expected):
    assert parse_mentions(values) == expected


@pytest.mark.parametrize(
    "value",
    [
        "<!channel>",
        "<!here>",
        "<!everyone>",
        "<!subteam^S123|@team>",
        "@channel",
        "@alice",
        "alice",
        "<#C123|general>",
        "B123ABCDEF",
        "<@u123abcdef>",
        "U1",
        "<@U123>",
        "UNKNOWN",
        "WHO",
        "USERS",
        "",
        42,
        None,
    ],
    ids=[
        "channel", "here", "everyone", "user-group", "plain-channel", "plain-name", "name", "channel-link", "bot-id", "lowercase",
        "too-short-id", "too-short-mention", "word-unknown", "word-who", "word-users", "empty", "int", "none",
    ],
)
def test_parse_mentions_rejects_anything_that_is_not_a_user(value):
    with pytest.raises(ReminderRequestError, match="by @mention"):
        parse_mentions([value])


def test_parse_mentions_rejects_more_people_than_the_limit():
    too_many = [f"U{n:09d}" for n in range(AgentConfigs.PR_REMINDER_MAX_MENTIONS + 1)]

    with pytest.raises(ReminderRequestError, match="at most 10 people"):
        parse_mentions(too_many)


def test_parse_mentions_counts_distinct_people_against_the_limit():
    assert len(parse_mentions(["U01ABCDEF"] * 50)) == 1
