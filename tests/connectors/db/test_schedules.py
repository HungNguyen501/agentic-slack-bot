from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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


def test_add_pr_reminder_inserts_a_pr_reminder_with_the_given_id():
    row = ROW | {"kind": "pr_reminder", "config": {"scope": "thread"}}
    with fake_connect(fetchone=row) as cur:
        created = schedules.add_pr_reminder("s1", "*/15 * * * *", "C1", "label", "bot-a", {"scope": "thread"})

    sql, params = cur.execute.call_args.args
    assert "'pr_reminder'" in sql
    assert params[:5] == ("s1", "*/15 * * * *", "C1", "label", "bot-a")
    assert params[5].obj == {"scope": "thread"}
    assert created.kind == "pr_reminder"


def test_set_schedule_status_only_ends_an_active_schedule():
    with fake_connect() as cur:
        schedules.set_schedule_status("s1", "completed")

    cur.execute.assert_called_once_with("UPDATE schedules SET status = %s WHERE id = %s AND status = 'active'", ("completed", "s1"))


def test_count_active_pr_reminders_is_scoped_to_bot_and_channel():
    with fake_connect(fetchone={"n": 3}) as cur:
        count = schedules.count_active_pr_reminders("bot-a", "C1")

    sql, params = cur.execute.call_args.args
    assert "kind = 'pr_reminder' AND status = 'active'" in sql
    assert params == ("bot-a", "C1")
    assert count == 3


def test_merge_schedule_config_merges_top_level_keys_into_the_existing_config():
    with fake_connect() as cur:
        schedules.merge_schedule_config("s1", {"has_reminded": True})

    sql, params = cur.execute.call_args.args
    assert sql == "UPDATE schedules SET config = config || %s WHERE id = %s"
    assert params[0].obj == {"has_reminded": True}
    assert params[1] == "s1"
