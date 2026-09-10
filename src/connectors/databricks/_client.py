"""Shared Databricks workspace host/token config, used by sql, jobs, and principals."""
import os

HOST = os.environ["DATABRICKS_HOST"].rstrip("/")
WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
TOKEN = os.environ["DATABRICKS_ACCESS_TOKEN"]


def auth_headers() -> dict:
    """Bearer auth header for the workspace PAT."""
    return {"Authorization": f"Bearer {TOKEN}"}
