from unittest.mock import MagicMock, patch

import httpx
import pytest

from connectors.databricks import sql


def response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def succeeded(rows, next_chunk=None):
    result = {"data_array": rows}
    if next_chunk:
        result["next_chunk_internal_link"] = next_chunk
    return {"status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": [{"name": "a"}, {"name": "b"}]}}, "result": result}


def test_fetch_rows_returns_every_row_as_a_dict_and_follows_chunks():
    with (
        patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response(succeeded([["1", None]], next_chunk="/api/chunk/1"))) as post,
        patch("connectors.databricks.sql.httpx.get", autospec=True, return_value=response({"data_array": [["3", "4"]]})) as get,
    ):
        result = sql.fetch_rows("select a, b from t;", max_rows=10)

    assert result.columns == ["a", "b"]
    assert result.rows == [{"a": "1", "b": None}, {"a": "3", "b": "4"}]
    payload = post.call_args.kwargs["json"]
    assert (payload["statement"], payload["row_limit"], payload["on_wait_timeout"]) == ("select a, b from t", 11, "CANCEL")
    assert get.call_args.args[0].endswith("/api/chunk/1")


def test_fetch_rows_refuses_anything_but_a_select_without_calling_databricks():
    with patch("connectors.databricks.sql.httpx.post", autospec=True) as post, pytest.raises(sql.DatabricksQueryError, match="SELECT"):
        sql.fetch_rows("drop table t", max_rows=10)

    post.assert_not_called()


def test_fetch_rows_refuses_a_result_over_the_row_limit_instead_of_truncating():
    with (
        patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response(succeeded([["1", "2"], ["3", "4"], ["5", "6"]]))),
        pytest.raises(sql.DatabricksQueryError, match="more than 2 rows"),
    ):
        sql.fetch_rows("select 1", max_rows=2)


@pytest.mark.parametrize(
    ("status", "message"),
    [({"state": "FAILED", "error": {"message": "boom"}}, "failed: boom"), ({"state": "CANCELED"}, "50s timeout")],
)
def test_fetch_rows_reports_a_statement_that_did_not_succeed(status, message):
    with (
        patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response({"status": status})),
        pytest.raises(sql.DatabricksQueryError, match=message),
    ):
        sql.fetch_rows("select 1", max_rows=10)


def test_fetch_rows_wraps_connection_errors():
    with (
        patch("connectors.databricks.sql.httpx.post", autospec=True, side_effect=httpx.ConnectError("down")),
        pytest.raises(sql.DatabricksQueryError, match="Could not reach Databricks"),
    ):
        sql.fetch_rows("select 1", max_rows=10)


def test_run_query_formats_rows_for_slack():
    with patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response(succeeded([["1", None]]))):
        assert sql.run_query("select a, b from t") == "```\na  b   \n-  ----\n1  NULL\n```\n_1 row(s)_"


def test_run_query_keeps_its_agent_facing_messages():
    with patch("connectors.databricks.sql.httpx.post", autospec=True) as post:
        assert sql.run_query("drop table t") == "Error: only SELECT queries are permitted."
        post.return_value = response({"status": {"state": "CANCELED"}})
        assert sql.run_query("select 1") == "Query was cancelled (exceeded 50s timeout)."
        post.return_value = response({"status": {"state": "FAILED", "error": {"message": "boom"}}})
        assert sql.run_query("select 1") == "Query failed: boom"


def test_both_entry_points_send_the_same_statement_request():
    with patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response(succeeded([["1", "2"]]))) as post:
        sql.run_query("select 1")
        sql.fetch_rows("select 1", max_rows=5)

    plain, fetched = (call.kwargs["json"] for call in post.call_args_list)
    assert fetched == plain | {"row_limit": 6}


def test_fetch_rows_refuses_duplicate_column_names_instead_of_dropping_values():
    payload = {"status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": [{"name": "id"}, {"name": "id"}]}}, "result": {"data_array": [["1", "2"]]}}
    with (
        patch("connectors.databricks.sql.httpx.post", autospec=True, return_value=response(payload)),
        pytest.raises(sql.DatabricksQueryError, match="duplicate column name.*id.*unique alias"),
    ):
        sql.fetch_rows("select 1 as id, 2 as id", max_rows=10)
