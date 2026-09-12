"""Shared Databricks workspace host/token config and SCIM pagination helper."""
import logging

import httpx

from common.configs import Configs

log = logging.getLogger("connectors.databricks")

HOST = Configs.DATABRICKS_HOST.rstrip("/")
WAREHOUSE_ID = Configs.DATABRICKS_WAREHOUSE_ID
TOKEN = Configs.DATABRICKS_ACCESS_TOKEN

_SCIM_PAGE_SIZE = 100
_SCIM_MAX_PAGES = 20  # safety cap: up to ~2000 resources


def auth_headers() -> dict:
    """Bearer auth header for the workspace PAT."""
    return {"Authorization": f"Bearer {TOKEN}"}


def list_scim_resources(resource_path: str) -> list[dict]:
    """Page through a workspace SCIM v2 collection and return all Resources.

    Args:
        resource_path: SCIM resource name, e.g. "Users", "ServicePrincipals", or "Groups".

    Returns:
        All resources across all pages, up to the safety cap (logs a warning if the cap is
        hit before totalResults is exhausted).
    """
    resources: list[dict] = []
    start_index = 1

    for page_num in range(_SCIM_MAX_PAGES):
        resp = httpx.get(
            f"{HOST}/api/2.0/preview/scim/v2/{resource_path}",
            headers=auth_headers(),
            params={"startIndex": start_index, "count": _SCIM_PAGE_SIZE},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        page = data.get("Resources", [])
        resources.extend(page)

        total = data.get("totalResults", 0)
        start_index += _SCIM_PAGE_SIZE
        if not page or start_index > total:
            return resources

        if page_num == _SCIM_MAX_PAGES - 1:
            log.warning(
                "Hit the %d-page safety cap listing %s; %d of %d fetched",
                _SCIM_MAX_PAGES, resource_path, len(resources), total,
            )

    return resources
