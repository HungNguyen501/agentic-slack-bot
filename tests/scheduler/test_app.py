import hashlib
from unittest.mock import MagicMock

from connectors.db.schedules import Schedule
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
    assert (kwargs["channel"], kwargs["question"], kwargs["bot_id"], kwargs["job_timeout"]) == ("C1", "what failed?", "bot-a", 120)


def test_a_pr_reminder_enqueues_the_reminder_job_with_its_schedule_id():
    queue = MagicMock()

    app._enqueue(queue, entry(kind="pr_reminder"))

    assert queue.enqueue.call_args.args == ("worker.tasks.process_pr_reminder",)
    kwargs = queue.enqueue.call_args.kwargs
    assert (kwargs["schedule_id"], kwargs["bot_id"]) == ("s1", "bot-a")
    assert "question" not in kwargs


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
