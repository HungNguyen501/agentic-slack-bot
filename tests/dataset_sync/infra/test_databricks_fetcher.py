from unittest.mock import patch

import pytest

from connectors.databricks import DatabricksQueryError, RowSet
from dataset_sync.core.errors import DatasetFetchError
from dataset_sync.core.models import FetchedRows
from dataset_sync.infra.databricks_fetcher import DatabricksDatasetFetcher


def test_fetch_returns_every_row_with_the_row_limit_as_a_keyword():
    with patch("dataset_sync.infra.databricks_fetcher.fetch_rows", autospec=True, return_value=RowSet(columns=["a"], rows=[{"a": "1"}])) as fetch:
        result = DatabricksDatasetFetcher().fetch("select a", 10)

    fetch.assert_called_once_with("select a", max_rows=10)
    assert result == FetchedRows(["a"], [{"a": "1"}])


def test_a_databricks_error_becomes_a_fetch_error_that_names_databricks():
    with (
        patch("dataset_sync.infra.databricks_fetcher.fetch_rows", autospec=True, side_effect=DatabricksQueryError("boom")),
        pytest.raises(DatasetFetchError, match="Databricks query failed: boom"),
    ):
        DatabricksDatasetFetcher().fetch("select a", 10)
