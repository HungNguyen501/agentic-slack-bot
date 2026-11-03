"""Databricks workspace SCIM writes: create service principals and manage group membership.

Like principals.py, real HTTP/transport failures propagate (RQ's retry/failure handling) rather
than being swallowed — this is called from the reviewer-approval workflow, not the LLM tool layer.
"""
import logging

from agent.access_control.principals import find_service_principal_by_email
from connectors.databricks import add_group_member, create_service_principal, list_scim_resources

log = logging.getLogger("agent.access_control.service_principals")


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
        sp = create_service_principal(display_name)
        log.info("Created service principal %s (id=%s)", display_name, sp.get("id"))

    current_group_ids = {g.get("value") for g in sp.get("groups", [])}
    for name, group_id in _find_group_ids(groups).items():
        if group_id in current_group_ids:
            continue
        add_group_member(group_id, sp["id"])
        log.info("Added %s to group %s", display_name, name)

    return sp
