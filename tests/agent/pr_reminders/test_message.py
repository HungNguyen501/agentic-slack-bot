from datetime import UTC, datetime, timedelta

import pytest

from agent.pr_reminders.message import escape, format_age, render_done_note, render_thread_message
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.status import Bucket, PrStatus

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
MENTIONS = ["U1", "U2"]


def status(number=1, title="Fix retry logic", age=timedelta(days=2), bucket=Bucket.WAITING):
    return PrStatus(PrLink(PrRef("org", "repo", number), "C1", "1.0", "1.0"), bucket, title, NOW - age)


@pytest.mark.parametrize(
    "age, expected",
    [
        (timedelta(minutes=5), "5m"),
        (timedelta(minutes=59), "59m"),
        (timedelta(hours=6, minutes=30), "6h"),
        (timedelta(days=2, hours=5), "2d"),
        (timedelta(minutes=-3), "0m"),
    ],
    ids=["minutes", "just-under-an-hour", "hours", "days", "clock-skew"],
)
def test_format_age(age, expected):
    assert format_age(NOW - age, NOW) == expected


def test_format_age_without_a_creation_time():
    assert format_age(None, NOW) == "?"


def test_escape_neutralises_slack_control_characters():
    assert escape("<!channel> & <@U1>") == "&lt;!channel&gt; &amp; &lt;@U1&gt;"


def test_render_thread_message_mentions_the_people_and_lists_waiting_prs():
    text = render_thread_message([status(1), status(2, "Add view", timedelta(hours=6))], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert text == (
        "<@U1> <@U2> :eyes: Waiting for review\n"
        "• <https://github.com/org/repo/pull/1|org/repo#1 Fix retry logic> — open 2d\n"
        "• <https://github.com/org/repo/pull/2|org/repo#2 Add view> — open 6h"
    )


def test_render_thread_message_never_lets_a_title_ping_anyone_else():
    text = render_thread_message([status(title="<!channel> hi <@U9> | x")], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "<!channel>" not in text
    assert "<@U9>" not in text
    assert text.splitlines()[0] == "<@U1> <@U2> :eyes: Waiting for review"
    assert "&lt;!channel&gt; hi &lt;@U9&gt; ¦ x" in text


def test_render_thread_message_lists_author_prs_below_the_ping():
    text = render_thread_message([status(1)], [status(2, bucket=Bucket.WAITING_ON_AUTHOR)], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "Changes requested, waiting on the author:" in text
    assert text.index("<@U1>") < text.index("Changes requested")


def test_render_thread_message_without_waiting_prs_does_not_ping():
    text = render_thread_message([], [status(2, bucket=Bucket.WAITING_ON_AUTHOR)], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "<@" not in text
    assert "waiting on the author" in text


def test_render_thread_message_for_a_recurring_reminder_shows_only_the_schedule_id():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a")

    assert text.splitlines()[-1] == "Schedule `3f2a` — ask me to remove it to stop. Reminders are only sent 07:00–22:00 Vietnam time (UTC+7)."


def test_render_thread_message_for_a_one_off_check_does_not_mention_active_hours():
    assert "Vietnam" not in render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None)


def test_render_thread_message_is_none_when_there_is_nothing_to_say():
    assert render_thread_message([], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a") is None


@pytest.mark.parametrize("scope, lookback, where", [("thread", None, "in this thread"), ("channel", 72, "in the last 72h")])
def test_render_done_note_names_where_it_looked(scope, lookback, where):
    note = render_done_note("3f2a", scope, lookback)

    assert where in note
    assert "`3f2a`" in note
