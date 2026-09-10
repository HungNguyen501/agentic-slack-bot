"""Databricks workspace SCIM lookups for users and service principals.

Unlike sql.py/jobs.py (which swallow errors into strings for the LLM tool-call layer), these
functions are called from worker business logic — so real HTTP/transport failures are left to
propagate (RQ's retry/failure handling), and None is reserved strictly for "no match found".
"""
import logging

import httpx

from ._client import HOST, auth_headers

log = logging.getLogger("connectors.databricks.principals")

_PAGE_SIZE = 100
_MAX_PAGES = 20  # safety cap: up to ~2000 principals


def _list_scim_resources(resource_path: str) -> list[dict]:
    """Page through a workspace SCIM v2 collection and return all Resources.

    Args:
        resource_path: SCIM resource name, e.g. "Users" or "ServicePrincipals".

    Returns:
        All resources across all pages, up to the safety cap (logs a warning if the cap is
        hit before totalResults is exhausted).
    """
    resources: list[dict] = []
    start_index = 1

    for page_num in range(_MAX_PAGES):
        resp = httpx.get(
            f"{HOST}/api/2.0/preview/scim/v2/{resource_path}",
            headers=auth_headers(),
            params={"startIndex": start_index, "count": _PAGE_SIZE},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        page = data.get("Resources", [])
        resources.extend(page)

        total = data.get("totalResults", 0)
        start_index += _PAGE_SIZE
        if not page or start_index > total:
            return resources

        if page_num == _MAX_PAGES - 1:
            log.warning("Hit the %d-page safety cap listing %s; %d of %d fetched", _MAX_PAGES, resource_path, len(resources), total)

    return resources


def find_user_by_email(email: str) -> dict | None:
    """Find a workspace user whose userName or any email exactly matches the given address (case-insensitive).

    Args:
        email: The email address submitted on the access request form.

    Returns:
        The matching SCIM User resource, or None if no user matches.
    """
    needle = email.strip().lower()
    for user in _list_scim_resources("Users"):
        candidates = [user.get("userName", "")] + [e.get("value", "") for e in user.get("emails", [])]
        if any(needle == c.lower() for c in candidates if c):
            return user
    return None


def find_service_principal_by_email(email: str) -> dict | None:
    """Find a service principal whose displayName is "svc-<email>" (case-insensitive, exact match).

    Args:
        email: The email address submitted on the access request form (a service principal's
            displayName is conventionally "svc-<email>").

    Returns:
        The matching SCIM ServicePrincipal resource, or None if no service principal matches.
    """
    needle = email.strip().lower()
    for sp in _list_scim_resources("ServicePrincipals"):
        display_name = sp.get("displayName", "").lower()
        if display_name.removeprefix("svc-") == needle:
            return sp
    return None
