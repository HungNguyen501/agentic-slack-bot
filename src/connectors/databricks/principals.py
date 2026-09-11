"""Databricks workspace SCIM lookups for users and service principals.

Unlike sql.py/jobs.py (which swallow errors into strings for the LLM tool-call layer), these
functions are called from worker business logic — so real HTTP/transport failures are left to
propagate (RQ's retry/failure handling), and None is reserved strictly for "no match found".
"""
from ._client import list_scim_resources


def _match_user(email: str, users: list[dict]) -> dict | None:
    needle = email.strip().lower()
    for user in users:
        candidates = [user.get("userName", "")] + [e.get("value", "") for e in user.get("emails", [])]
        if any(needle == c.lower() for c in candidates if c):
            return user
    return None


def _match_service_principal(email: str, service_principals: list[dict]) -> dict | None:
    needle = email.strip().lower()
    for sp in service_principals:
        display_name = sp.get("displayName", "").lower()
        if display_name.removeprefix("svc-") == needle:
            return sp
    return None


def find_user_by_email(email: str) -> dict | None:
    """Find a workspace user whose userName or any email exactly matches the given address (case-insensitive).

    Args:
        email: The email address submitted on the access request form.

    Returns:
        The matching SCIM User resource, or None if no user matches.
    """
    return _match_user(email, list_scim_resources("Users"))


def find_service_principal_by_email(email: str) -> dict | None:
    """Find a service principal whose displayName is "svc-<email>" (case-insensitive, exact match).

    Args:
        email: The email address submitted on the access request form (a service principal's
            displayName is conventionally "svc-<email>").

    Returns:
        The matching SCIM ServicePrincipal resource, or None if no service principal matches.
    """
    return _match_service_principal(email, list_scim_resources("ServicePrincipals"))


def find_users_by_emails(emails: list[str]) -> dict[str, dict | None]:
    """Batch version of find_user_by_email — lists workspace Users once and matches every
    requested email against that single list, instead of one full paginated listing per email
    (which is what made multi-email form submissions time out).

    Args:
        emails: Email addresses submitted on the access request form.

    Returns:
        {email: matching SCIM User resource or None}, one entry per input email.
    """
    users = list_scim_resources("Users")
    return {email: _match_user(email, users) for email in emails}


def find_service_principals_by_emails(emails: list[str]) -> dict[str, dict | None]:
    """Batch version of find_service_principal_by_email — lists workspace ServicePrincipals
    once and matches every requested email against that single list.

    Args:
        emails: Email addresses submitted on the access request form.

    Returns:
        {email: matching SCIM ServicePrincipal resource or None}, one entry per input email.
    """
    service_principals = list_scim_resources("ServicePrincipals")
    return {email: _match_service_principal(email, service_principals) for email in emails}
