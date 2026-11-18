import pytest

from schedules.core.rules import is_same_reminder

NEW = {"scope": "channel", "mentions": ["U01ABCDEF", "U02GHIJKL"], "origin_channel": "C9", "origin_thread_ts": "1.0", "lookback_hours": 72}


@pytest.mark.parametrize(
    "existing",
    [
        NEW,
        NEW | {"mentions": ["U02GHIJKL", "U01ABCDEF"]},
        NEW | {"lookback_hours": 168},
        NEW | {"origin_thread_ts": "9.9", "origin_channel": "C7"},
    ],
    ids=["identical", "people-in-another-order", "different-lookback", "asked-from-another-thread"],
)
def test_the_same_scope_and_people_is_the_same_job(existing):
    assert is_same_reminder(existing, NEW)


@pytest.mark.parametrize(
    "existing",
    [
        NEW | {"mentions": ["U01ABCDEF"]},
        NEW | {"mentions": ["U01ABCDEF", "U02GHIJKL", "U03MNOPQR"]},
        NEW | {"mentions": ["U01ABCDEF", "U09ZZZZZZ"]},
        NEW | {"scope": "thread"},
        {"scope": "channel", "origin_channel": "C9", "origin_thread_ts": "1.0"},
    ],
    ids=["fewer-people", "more-people", "different-people", "different-scope", "no-mentions-stored"],
)
def test_different_people_or_scope_is_a_different_job(existing):
    assert not is_same_reminder(existing, NEW)


def test_a_thread_scope_reminder_is_the_same_job_only_for_the_same_thread():
    thread = NEW | {"scope": "thread", "lookback_hours": None}

    assert is_same_reminder(thread, thread)
    assert not is_same_reminder(thread, thread | {"origin_thread_ts": "2.0"})
