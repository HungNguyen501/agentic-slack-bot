"""Shared Databricks workspace host/token config and error helper."""
import httpx

from common.configs import Configs

HOST = Configs.DATABRICKS_HOST.rstrip("/")
WAREHOUSE_ID = Configs.DATABRICKS_WAREHOUSE_ID
TOKEN = Configs.DATABRICKS_ACCESS_TOKEN


def auth_headers() -> dict:
    """Bearer auth header for the workspace PAT."""
    return {"Authorization": f"Bearer {TOKEN}"}


def raise_for_status_with_body(resp: httpx.Response) -> None:
    """Like resp.raise_for_status(), but includes the response body in the error message.

    Databricks' APIs return a JSON error `detail` explaining exactly what's wrong with a
    request (e.g. an unsupported Patch shape) — httpx's default error message only shows the
    status code and URL, which isn't enough to debug a 400 from the logs alone.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise httpx.HTTPStatusError(f"{exc}: {resp.text}", request=exc.request, response=exc.response) from exc
