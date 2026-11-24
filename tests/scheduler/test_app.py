import hashlib
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from schedules.core.models import Schedule
from scheduler import app


def entry(**overrides):
    fields = {"id": "s1", "cron": "*/15 * * * *", "channel": "C1", "question": "q", "bot_id": "bot-a"} | overrides
    return Schedule(**fields)


def test_a_question_schedule_enqueues_the_scheduled_question_job():
    queue = MagicMock()

    app._enqueue(queue, entry(question="what failed?"))

    queue.enqueue.assert_called_once()
    assert queue.enqueue.call_args.args == ("worker.tasks.process_scheduled_question",)
    kwargs = queue.enqueue.call_args.kwargs
    assert (kwargs["channel"], kwargs["question"], kwargs["bot_id"], kwargs["job_timeout"]) == ("C1", "what failed?", "bot-a", 600)
    assert kwargs["retry"].max == 3


def test_a_pr_reminder_enqueues_the_reminder_job_with_its_schedule_id():
    queue = MagicMock()

    app._enqueue(queue, entry(kind="pr_reminder"))

    assert queue.enqueue.call_args.args == ("worker.tasks.process_pr_reminder",)
    kwargs = queue.enqueue.call_args.kwargs
    assert (kwargs["schedule_id"], kwargs["bot_id"]) == ("s1", "bot-a")
    assert "question" not in kwargs
    assert "retry" not in kwargs  # a retry after posting would repeat the mentions; the next tick is the retry


def test_an_unowned_schedule_runs_under_the_default_bot():
    queue = MagicMock()

    app._enqueue(queue, Schedule(id="s1", cron="* * * * *", channel="C1", question="q"))

    assert queue.enqueue.call_args.kwargs["bot_id"] == "default"


def test_question_schedules_keep_their_existing_dedup_key():
    # Must stay "<cron>:<channel>:<question>" hashed: changing it would make every existing schedule fire twice on deploy.
    expected = hashlib.sha256(b"*/15 * * * *:C1:q").hexdigest()[:16]

    assert app._schedule_key(entry()) == f"scheduler:last_fired:{expected}"


def test_two_pr_reminders_in_one_channel_do_not_share_a_dedup_key():
    first = Schedule(id="s1", cron="*/15 * * * *", channel="C1", question="label", kind="pr_reminder")
    second = Schedule(id="s2", cron="*/15 * * * *", channel="C1", question="label", kind="pr_reminder")

    assert app._schedule_key(first) != app._schedule_key(second)


def test_a_dataset_sync_enqueues_the_sync_job_with_its_schedule_id_and_no_retry():
    queue = MagicMock()

    app._enqueue(queue, entry(kind="dataset_sync"))

    assert queue.enqueue.call_args.args == ("worker.tasks.process_dataset_sync",)
    kwargs = queue.enqueue.call_args.kwargs
    assert (kwargs["schedule_id"], kwargs["bot_id"]) == ("s1", "bot-a")
    assert "retry" not in kwargs


def test_dataset_syncs_are_keyed_on_their_id_so_two_in_one_channel_do_not_collide():
    first = Schedule(id="s1", cron="0 2 * * *", channel="C1", question="label", kind="dataset_sync")
    second = Schedule(id="s2", cron="0 2 * * *", channel="C1", question="label", kind="dataset_sync")

    assert app._schedule_key(first) != app._schedule_key(second)
    assert app._schedule_key(first) != app._schedule_key(Schedule(id="s1", cron="0 2 * * *", channel="C1", question="label", kind="pr_reminder"))


def redis_without_history():
    client = MagicMock()
    client.get.return_value = None
    return client


# The loop wakes a little after the minute a cron fires, never exactly on it, so every "now" here is 30 seconds past.
# 05:00 UTC is 12:00 in Vietnam (UTC+7).
@pytest.mark.parametrize(
    "timezone, now_utc, due",
    [
        ("UTC", datetime(2026, 11, 4, 19, 0, 30), True),
        ("UTC", datetime(2026, 11, 4, 5, 0, 30), False),  # before 07:00 UTC
        ("UTC", datetime(2026, 11, 4, 22, 0, 30), False),  # after 21:59 UTC
        ("Asia/Ho_Chi_Minh", datetime(2026, 11, 4, 5, 0, 30), True),  # 12:00 Vietnam, which is before 07:00 only in UTC
        ("Asia/Ho_Chi_Minh", datetime(2026, 11, 4, 0, 0, 30), True),  # 07:00 Vietnam: the first hour of the window
        ("Asia/Ho_Chi_Minh", datetime(2026, 11, 3, 23, 0, 30), False),  # 06:00 Vietnam: before the window
        ("Asia/Ho_Chi_Minh", datetime(2026, 11, 4, 15, 0, 30), False),  # 22:00 Vietnam: after the window, though 15:00 UTC is "daytime"
    ],
    ids=["utc-evening", "utc-before", "utc-after", "vn-midday", "vn-07:00", "vn-06:00", "vn-22:00"],
)
def test_a_schedule_is_due_when_its_cron_fires_in_its_own_timezone(timezone, now_utc, due):
    reminder = entry(cron="0 7-21 * * *", timezone=timezone)

    assert app._should_fire(reminder, now_utc, redis_without_history()) is due


def test_a_schedule_with_an_unknown_timezone_never_fires_instead_of_crashing_the_loop():
    assert app._should_fire(entry(timezone="Mars/Olympus"), datetime(2026, 11, 4, 5, 0, 30), redis_without_history()) is False


def test_a_firing_already_recorded_in_redis_is_not_repeated_whatever_the_timezone():
    now = datetime(2026, 11, 4, 5, 0, 30)  # just after 12:00 in Vietnam, the firing of "0 12 * * *" there
    client = MagicMock()
    client.get.return_value = now.isoformat().encode()  # the scheduler stores naive UTC

    assert app._should_fire(entry(cron="0 12 * * *", timezone="Asia/Ho_Chi_Minh"), now, client) is False


def test_the_recorded_time_of_an_earlier_firing_does_not_block_the_next_one_in_another_timezone():
    now = datetime(2026, 11, 4, 5, 0, 30)
    client = MagicMock()
    client.get.return_value = datetime(2026, 11, 3, 5, 0).isoformat().encode()  # yesterday's firing

    assert app._should_fire(entry(cron="0 12 * * *", timezone="Asia/Ho_Chi_Minh"), now, client) is True


def test_a_firing_before_a_dst_fall_back_is_not_mistaken_for_a_recent_one_in_the_repeated_hour():
    # New York leaves daylight time on 2026-11-01: 01:00-01:59 happens twice. "59 0 * * *" last fired at 00:59 EDT (04:59 UTC);
    # at 01:01 EST (06:01 UTC) the wall clocks are two minutes apart but a full hour and two minutes really passed.
    reminder = entry(cron="59 0 * * *", timezone="America/New_York")

    assert app._should_fire(reminder, datetime(2026, 11, 1, 6, 1), redis_without_history()) is False


def test_a_firing_just_before_a_dst_fall_back_is_still_due_right_after_it():
    reminder = entry(cron="59 0 * * *", timezone="America/New_York")

    assert app._should_fire(reminder, datetime(2026, 11, 1, 4, 59, 30), redis_without_history()) is True
