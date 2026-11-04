from datetime import UTC, datetime, timedelta, timezone

import pytest

from agent.pr_reminders.limits import ReminderRequestError, is_reminder_expired, is_within_active_hours, validate_reminder_cron
from common.configs import AgentConfigs

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", ["*/30 * * * *", "0 * * * *", "0 9 * * 1-5", "0,30 * * * *", "15 */2 * * *"])
def test_validate_reminder_cron_accepts_expressions_at_or_above_the_minimum_interval(cron):
    validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize(
    "cron",
    ["* * * * *", "*/5 * * * *", "*/15 * * * *", "*/29 * * * *", "0,29 * * * *", "0,10,40 * * * *", "*/20 9-18 * * 1-5"],
    ids=["every-minute", "every-5m", "every-15m", "every-29m", "29m-gap", "irregular", "business-hours-20m"],
)
def test_validate_reminder_cron_rejects_expressions_that_fire_more_often_than_every_30_minutes(cron):
    with pytest.raises(ReminderRequestError, match="at most every 30 minutes"):
        validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize("cron", ["", "*/15 * * *", "0 0 * * * *", "not a cron at all", "99 * * * *"], ids=["empty", "4-fields", "6-fields", "garbage", "out-of-range"])
def test_validate_reminder_cron_rejects_malformed_expressions(cron):
    with pytest.raises(ReminderRequestError):
        validate_reminder_cron(cron, NOW)


@pytest.mark.parametrize(
    "age, expired",
    [
        (timedelta(days=1), False),
        (timedelta(days=AgentConfigs.PR_REMINDER_LIFETIME_DAYS, minutes=-1), False),
        (timedelta(days=AgentConfigs.PR_REMINDER_LIFETIME_DAYS, minutes=1), True),
    ],
    ids=["fresh", "just-inside", "just-past"],
)
def test_is_reminder_expired_uses_the_lifetime(age, expired):
    assert is_reminder_expired(str(NOW - age), NOW) is expired


def test_is_reminder_expired_is_false_without_a_creation_time():
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
