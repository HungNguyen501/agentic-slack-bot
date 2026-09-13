"""Databricks OAuth secret generation for service principals.

The `/api/2.0/accounts/servicePrincipals/{id}/credentials/secrets` path looks
account-scoped but is a workspace-level proxy — it's called against the workspace
host with the workspace PAT, same as every other call in this package (verified
against the databricks-sdk-py source for ServicePrincipalSecretsProxyAPI, since
Databricks' own docs pages for this endpoint were unreliable). No account-level
credentials are needed.
"""
import httpx

from ._client import HOST, auth_headers, raise_for_status_with_body


def create_secret(service_principal_id: str) -> dict:
    """Create a new OAuth client secret for a service principal.

    Args:
        service_principal_id: The SCIM `id` of the service principal (not applicationId).

    Returns:
        Raw response: id, secret, secret_hash, status, create_time, update_time, expire_time.
        `secret` is only ever present in this create response — Databricks never returns
        a secret's plaintext value again after this call.
    """
    resp = httpx.post(
        f"{HOST}/api/2.0/accounts/servicePrincipals/{service_principal_id}/credentials/secrets",
        headers={**auth_headers(), "Content-Type": "application/json"},
        json={},
        timeout=30.0,
    )
    raise_for_status_with_body(resp)
    return resp.json()
