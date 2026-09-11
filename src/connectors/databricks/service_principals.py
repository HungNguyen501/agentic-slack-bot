"""Databricks workspace SCIM writes: create service principals and manage group membership.

Like principals.py, real HTTP/transport failures propagate (RQ's retry/failure handling) rather
than being swallowed — this is called from the reviewer-approval workflow, not the LLM tool layer.
"""
import logging

import httpx

from ._client import HOST, auth_headers, list_scim_resources
from .principals import find_service_principal_by_email

log = logging.getLogger("connectors.databricks.service_principals")


def _raise_for_status_with_body(resp: httpx.Response) -> None:
    """Like resp.raise_for_status(), but includes the response body in the error message.

    Databricks' SCIM API returns a JSON error `detail` explaining exactly what's wrong with a
    request (e.g. an unsupported Patch shape) — httpx's default error message only shows the
    status code and URL, which isn't enough to debug a 400 from the logs alone.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise httpx.HTTPStatusError(f"{exc}: {resp.text}", request=exc.request, response=exc.response) from exc


def list_service_principals() -> list[dict]:
    """List every service principal in the workspace, each with its current group memberships.

    Returns:
        Raw SCIM ServicePrincipal resources (id, applicationId, displayName, active, groups, ...).
    """
    return list_scim_resources("ServicePrincipals")


def _find_group_ids(group_names: list[str]) -> dict[str, str]:
    """Resolve group display names to their SCIM group ids.

    Args:
        group_names: Group display names requested on the access request form.

    Returns:
        {display_name: group_id} for every name that was found; silently omits any that
        don't match an existing workspace group.
    """
    groups_by_name = {g.get("displayName"): g.get("id") for g in list_scim_resources("Groups")}
    resolved = {}
    for name in group_names:
        group_id = groups_by_name.get(name)
        if group_id is None:
            log.warning("No workspace group named %r found; skipping", name)
            continue
        resolved[name] = group_id
    return resolved


def _add_member_to_group(group_id: str, member_id: str) -> None:
    """Add a principal to an account-level SCIM group via a Patch operation.

    Groups like gpt-users are account-level (federated into the workspace) — the workspace
    preview SCIM Groups endpoint can list/read them (used by _find_group_ids) but rejects
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
    _raise_for_status_with_body(resp)


def _create_service_principal(display_name: str) -> dict:
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
    _raise_for_status_with_body(resp)
    return resp.json()


def find_or_create_service_principal(email: str, groups: list[str]) -> dict:
    """Find the "svc-<email>" service principal, or create it, and ensure it's in the requested groups.

    Args:
        email: The verified user_email the access request was submitted for.
        groups: Group display names the principal should belong to (from the form).

    Returns:
        The service principal's SCIM resource (id, applicationId, displayName, ...).
    """
    display_name = f"svc-{email}"
    sp = find_service_principal_by_email(email)
    if sp is None:
        sp = _create_service_principal(display_name)
        log.info("Created service principal %s (id=%s)", display_name, sp.get("id"))

    current_group_ids = {g.get("value") for g in sp.get("groups", [])}
    for name, group_id in _find_group_ids(groups).items():
        if group_id in current_group_ids:
            continue
        _add_member_to_group(group_id, sp["id"])
        log.info("Added %s to group %s", display_name, name)

    return sp
