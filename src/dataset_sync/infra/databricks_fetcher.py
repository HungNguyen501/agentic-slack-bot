from connectors.databricks import DatabricksQueryError, fetch_rows
from dataset_sync.core.errors import DatasetFetchError
from dataset_sync.core.models import FetchedRows


class DatabricksDatasetFetcher:
    def fetch(self, sql: str, max_rows: int) -> FetchedRows:
        try:
            result = fetch_rows(sql, max_rows=max_rows)
        except DatabricksQueryError as exc:
            raise DatasetFetchError(f"Databricks query failed: {exc}") from exc
        return FetchedRows(result.columns, result.rows)
