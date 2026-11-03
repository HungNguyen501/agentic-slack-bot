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
