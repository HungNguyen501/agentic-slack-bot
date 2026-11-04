from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from connectors.db import schedules

ROW = {"id": "s1", "cron": "0 9 * * *", "channel": "C1", "question": "q", "bot_id": "bot-a"}


@contextmanager
def fake_connect(fetchone=None, rowcount=1):
    cur = MagicMock(fetchone=MagicMock(return_value=fetchone), rowcount=rowcount)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    with patch("connectors.db.schedules.connect", autospec=True) as connect:
        connect.return_value.__enter__.return_value = conn
        yield cur


def test_update_schedule_is_scoped_to_the_calling_bot():
    with fake_connect(fetchone=ROW) as cur:
        schedules.update_schedule("s1", cron="0 10 * * *", bot_id="bot-a")

    sql, params = cur.execute.call_args.args
    assert "WHERE id = %s AND (bot_id = %s OR bot_id IS NULL)" in sql
    assert params == ["0 10 * * *", "s1", "bot-a"]


def test_update_schedule_returns_none_when_row_belongs_to_another_bot():
    with fake_connect(fetchone=None):
        assert schedules.update_schedule("s1", cron="0 10 * * *", bot_id="bot-b") is None


def test_update_schedule_without_fields_does_not_query():
    with fake_connect() as cur:
        assert schedules.update_schedule("s1", bot_id="bot-a") is None

    cur.execute.assert_not_called()


def test_remove_schedule_is_scoped_to_the_calling_bot():
    with fake_connect(rowcount=0) as cur:
        deleted = schedules.remove_schedule("s1", bot_id="bot-b")

    cur.execute.assert_called_once_with("DELETE FROM schedules WHERE id = %s AND (bot_id = %s OR bot_id IS NULL)", ("s1", "bot-b"))
    assert deleted is False


def test_update_schedule_never_touches_pr_reminders():
    with fake_connect(fetchone=ROW) as cur:
        schedules.update_schedule("s1", cron="0 10 * * *", bot_id="bot-a")

    assert "AND kind = 'question'" in cur.execute.call_args.args[0]


def test_get_schedules_without_filters_reads_everything():
    with fake_connect() as cur:
        cur.fetchall.return_value = []
        schedules.get_schedules()

    sql, params = cur.execute.call_args.args
    assert "WHERE" not in sql
    assert params == []


def test_get_schedules_active_only_skips_ended_reminders():
    with fake_connect() as cur:
        cur.fetchall.return_value = []
        schedules.get_schedules(active_only=True)

    assert "WHERE status = 'active'" in cur.execute.call_args.args[0]


def test_get_schedules_for_a_bot_keeps_the_legacy_unowned_rows():
    with fake_connect() as cur:
        cur.fetchall.return_value = []
        schedules.get_schedules("bot-a", active_only=True)

    sql, params = cur.execute.call_args.args
    assert "WHERE (bot_id = %s OR bot_id IS NULL) AND status = 'active'" in sql
    assert params == ["bot-a"]


def test_a_row_without_the_new_columns_is_a_live_question_schedule():
    with fake_connect() as cur:
        cur.fetchall.return_value = [ROW]
        [schedule] = schedules.get_schedules()

    assert (schedule.kind, schedule.status, schedule.config) == ("question", "active", {})


def test_a_pr_reminder_row_keeps_its_kind_status_and_config():
    row = ROW | {"kind": "pr_reminder", "status": "completed", "config": {"scope": "thread"}}
    with fake_connect(fetchone=row):
        schedule = schedules.get_schedule("s1")

    assert (schedule.kind, schedule.status, schedule.config) == ("pr_reminder", "completed", {"scope": "thread"})


def test_get_schedule_returns_none_for_an_unknown_id():
    with fake_connect(fetchone=None):
        assert schedules.get_schedule("nope") is None


NEW_CONFIG = {"scope": "channel", "mentions": ["U01ABCDEF", "U02GHIJKL"], "origin_channel": "C9", "origin_thread_ts": "1.0", "lookback_hours": 72}


def add(max_active=5, config=NEW_CONFIG):
    return schedules.add_pr_reminder("s-new", "*/30 * * * *", "C1", "label", "bot-a", config, max_active=max_active)


def reminder_row(config, id="s-old", cron="*/30 * * * *"):
    return ROW | {"id": id, "cron": cron, "kind": "pr_reminder", "config": config}


def test_add_pr_reminder_inserts_a_pr_reminder_with_the_given_id():
    with fake_connect() as cur:
        cur.fetchall.return_value = []
        cur.fetchone.return_value = reminder_row(NEW_CONFIG, id="s-new")
        added = add()

    sql, params = cur.execute.call_args.args
    assert "'pr_reminder'" in sql
    assert params[:5] == ("s-new", "*/30 * * * *", "C1", "label", "bot-a")
    assert params[5].obj == NEW_CONFIG
    assert added.created is True
    assert added.schedule.kind == "pr_reminder"


def test_add_pr_reminder_looks_up_and_inserts_in_one_transaction_under_a_lock_on_bot_and_channel():
    with fake_connect() as cur:
        cur.fetchall.return_value = []
        cur.fetchone.return_value = reminder_row(NEW_CONFIG)
        add()

    statements = [call.args[0] for call in cur.execute.call_args_list]
    assert statements[0] == "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
    assert cur.execute.call_args_list[0].args[1] == ("pr_reminder:bot-a:C1",)
    assert "kind = 'pr_reminder' AND status = 'active' AND bot_id = %s AND channel = %s" in statements[1]
    assert cur.execute.call_args_list[1].args[1] == ("bot-a", "C1")
    assert statements[2].startswith("INSERT INTO schedules")


def other_reminder(n):
    return reminder_row({"scope": "channel", "mentions": [f"U0{n}ZZZZZZ"], "origin_channel": "C9", "origin_thread_ts": "1.0"}, id=f"s{n}")


@pytest.mark.parametrize("existing", [5, 6], ids=["at-the-limit", "over-the-limit"])
def test_add_pr_reminder_inserts_nothing_and_returns_none_when_the_quota_is_used_by_other_reminders(existing):
    with fake_connect() as cur:
        cur.fetchall.return_value = [other_reminder(n) for n in range(existing)]
        added = add(max_active=5)

    assert added is None
    assert not any(call.args[0].startswith("INSERT") for call in cur.execute.call_args_list)


def test_add_pr_reminder_allows_the_last_slot_under_the_quota():
    with fake_connect() as cur:
        cur.fetchall.return_value = [other_reminder(n) for n in range(4)]
        cur.fetchone.return_value = reminder_row(NEW_CONFIG, id="s-new")
        assert add(max_active=5).created is True


@pytest.mark.parametrize(
    "existing_config",
    [
        NEW_CONFIG,
        NEW_CONFIG | {"mentions": ["U02GHIJKL", "U01ABCDEF"]},
        NEW_CONFIG | {"lookback_hours": 168},
        NEW_CONFIG | {"origin_thread_ts": "9.9", "origin_channel": "C7"},
    ],
    ids=["identical", "people-in-another-order", "different-lookback", "asked-from-another-thread"],
)
def test_add_pr_reminder_returns_the_existing_reminder_instead_of_creating_a_duplicate(existing_config):
    with fake_connect() as cur:
        cur.fetchall.return_value = [reminder_row(existing_config, cron="0 9 * * *")]
        added = add()

    assert added.created is False
    assert (added.schedule.id, added.schedule.cron) == ("s-old", "0 9 * * *")  # a different cron is still the same job
    assert not any(call.args[0].startswith("INSERT") for call in cur.execute.call_args_list)


def test_add_pr_reminder_returns_the_existing_one_even_when_the_quota_is_full():
    with fake_connect() as cur:
        cur.fetchall.return_value = [other_reminder(n) for n in range(4)] + [reminder_row(NEW_CONFIG)]
        added = add(max_active=5)

    assert added.created is False


@pytest.mark.parametrize(
    "existing_config",
    [
        NEW_CONFIG | {"mentions": ["U01ABCDEF"]},
        NEW_CONFIG | {"mentions": ["U01ABCDEF", "U02GHIJKL", "U03MNOPQR"]},
        NEW_CONFIG | {"mentions": ["U01ABCDEF", "U09ZZZZZZ"]},
        NEW_CONFIG | {"scope": "thread"},
        {"scope": "channel", "origin_channel": "C9", "origin_thread_ts": "1.0"},
    ],
    ids=["fewer-people", "more-people", "different-people", "different-scope", "no-mentions-stored"],
)
def test_add_pr_reminder_creates_a_new_reminder_when_the_job_differs(existing_config):
    with fake_connect() as cur:
        cur.fetchall.return_value = [reminder_row(existing_config)]
        cur.fetchone.return_value = reminder_row(NEW_CONFIG, id="s-new")
        added = add()

    assert added.created is True


def test_a_thread_scope_reminder_is_the_same_job_only_for_the_same_thread():
    thread_config = NEW_CONFIG | {"scope": "thread", "lookback_hours": None}
    with fake_connect() as cur:
        cur.fetchall.return_value = [reminder_row(thread_config)]
        same = add(config=thread_config)
    with fake_connect() as cur:
        cur.fetchall.return_value = [reminder_row(thread_config)]
        cur.fetchone.return_value = reminder_row(thread_config, id="s-new")
        other_thread = add(config=thread_config | {"origin_thread_ts": "2.0"})

    assert same.created is False
    assert other_thread.created is True


def test_set_schedule_status_only_ends_an_active_schedule():
    with fake_connect() as cur:
        schedules.set_schedule_status("s1", "completed")

    cur.execute.assert_called_once_with("UPDATE schedules SET status = %s WHERE id = %s AND status = 'active'", ("completed", "s1"))


def test_merge_schedule_config_merges_top_level_keys_into_the_existing_config():
    with fake_connect() as cur:
        schedules.merge_schedule_config("s1", {"has_reminded": True})

    sql, params = cur.execute.call_args.args
    assert sql == "UPDATE schedules SET config = config || %s WHERE id = %s"
    assert params[0].obj == {"has_reminded": True}
    assert params[1] == "s1"
