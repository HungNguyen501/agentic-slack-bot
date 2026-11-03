"""Databricks workspace SCIM v2 client — raw collection listing and writes, no business logic.

Real HTTP/transport failures propagate to the caller (RQ's retry/failure handling) rather than
being swallowed into strings like sql.py/jobs.py do for the LLM tool-call layer.
"""
import logging

import httpx

from .client import HOST, auth_headers, raise_for_status_with_body

log = logging.getLogger("connectors.databricks.scim")

_SCIM_PAGE_SIZE = 100
_SCIM_MAX_PAGES = 20  # safety cap: up to ~2000 resources


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


def create_service_principal(display_name: str) -> dict:
    """Create a new service principal in the workspace."""
    resp = httpx.post(
        f"{HOST}/api/2.0/preview/scim/v2/ServicePrincipals",
        headers={**auth_headers(), "Content-Type": "application/json"},
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
            "displayName": display_name,
            "active": True,
        },
        timeout=30.0,
    )
    raise_for_status_with_body(resp)
    return resp.json()


def add_group_member(group_id: str, member_id: str) -> None:
    """Add a principal to an account-level SCIM group via a Patch operation.

    Groups like gpt-users are account-level (federated into the workspace) — the workspace
    preview SCIM Groups endpoint can list/read them (see list_scim_resources) but rejects
    membership PATCHes with "can only be managed in account". Membership writes have to go
    through the account SCIM proxy instead, matching the org's existing access_control.py
    reference (add_users_to_group/remove_user_from_group), body shape included.
    """
    resp = httpx.patch(
        f"{HOST}/api/2.0/account/scim/v2/Groups/{group_id}",
        headers={**auth_headers(), "Content-Type": "application/json"},
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "value": {"members": [{"value": member_id}]}}],
        },
        timeout=30.0,
    )
    raise_for_status_with_body(resp)
