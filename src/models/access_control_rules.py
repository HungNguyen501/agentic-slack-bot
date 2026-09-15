"""Renders the two data-platform config files a service-principal request approval touches:
rules_v2.yaml (an appended row) and service_principals.yaml (a fully regenerated snapshot).
"""
import yaml

from .access_request_submission import VerifiedAccessRequest


def format_rules_entry(v: VerifiedAccessRequest) -> str:
    """Build one rules_v2.yaml list entry as plain text, matching the file's existing style.

    Built with plain string formatting rather than a YAML dump so appending it produces a
    minimal diff — the rest of the (very large) file is never re-serialized.

    Args:
        v: The verified, approved access request.

    Returns:
        A text block (with a trailing newline) ready to append to the file's list of rules.
    """
    items = v.field_items()
    lines = [f"  - {items[0][0]}: {items[0][1]}"] + [f"    {key}: {value}" for key, value in items[1:]]
    return "\n".join(lines) + "\n"


def format_service_principals_snapshot(service_principals: list[dict]) -> str:
    """Render every workspace service principal as service_principals.yaml's full content.

    Args:
        service_principals: Raw SCIM ServicePrincipal resources, as returned by
            connectors.databricks.list_service_principals().

    Returns:
        The complete new file content (alphabetical keys, groups sorted by display name),
        matching the existing generator's style exactly.
    """
    records = [
        {
            "active": sp.get("active", True),
            "applicationId": sp.get("applicationId"),
            "displayName": sp.get("displayName"),
            **({"entitlements": sp["entitlements"]} if sp.get("entitlements") else {}),
            "groups": sorted(
                [
                    {"$ref": g.get("$ref"), "display": g.get("display"), "type": g.get("type"), "value": g.get("value")}
                    for g in sp.get("groups", [])
                ],
                key=lambda g: g["display"],
            ),
            "id": sp.get("id"),
        }
        for sp in service_principals
    ]
    return yaml.dump(records, sort_keys=True, default_flow_style=False)
