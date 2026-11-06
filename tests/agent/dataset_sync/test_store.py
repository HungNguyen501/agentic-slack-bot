from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agent.dataset_sync import store


@contextmanager
def fake_connect(fetchone=None, rowcount=1):
    cur = MagicMock(fetchone=MagicMock(return_value=fetchone), rowcount=rowcount)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    with patch("agent.dataset_sync.store.connect", autospec=True) as connect:
        connect.return_value.__enter__.return_value = conn
        yield cur


def test_get_rows_returns_the_last_good_snapshot():
    with fake_connect(fetchone={"synced_rows": [{"a": "1"}], "last_synced_at": datetime(2026, 11, 5, tzinfo=UTC)}):
        assert store.get_rows("d") == [{"a": "1"}]


@pytest.mark.parametrize("row", [None, {"synced_rows": [], "last_synced_at": None}])
def test_get_rows_refuses_an_unknown_or_never_synced_dataset(row):
    with fake_connect(fetchone=row), pytest.raises(store.DatasetNotSyncedError, match="has not been synced yet"):
        store.get_rows("d")


def test_save_snapshot_replaces_the_rows_in_one_update():
    with fake_connect() as cur:
        store.save_snapshot("d", ["a"], [{"a": "1"}])

    sql, params = cur.execute.call_args.args
    assert sql.startswith("UPDATE synced_datasets SET column_names") and sql.endswith("WHERE dataset = %s")
    assert params[0] == ["a"] and params[2] == 1 and params[3] == "d"


def test_save_snapshot_for_a_deleted_dataset_raises_instead_of_pretending_to_succeed():
    with fake_connect(rowcount=0), pytest.raises(store.DatasetNotSyncedError, match="no longer exists"):
        store.save_snapshot("d", ["a"], [{"a": "1"}])


def test_dataset_lock_takes_a_transaction_scoped_lock_per_dataset_and_holds_it_for_the_block():
    with fake_connect(fetchone={"locked": True}) as cur:
        with store.dataset_lock("d"):
            assert cur.execute.call_count == 1
        sql, params = cur.execute.call_args.args

    assert "pg_try_advisory_xact_lock" in sql and params == ("dataset_sync:d",)


def test_dataset_lock_refuses_instead_of_waiting_when_another_sync_holds_it():
    with fake_connect(fetchone={"locked": False}), pytest.raises(store.DatasetBusyError, match="already running"), store.dataset_lock("d"):
        pytest.fail("the block must not run")
