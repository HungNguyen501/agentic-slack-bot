"""Fills the data-platform repo's PR template for an approved access request.

The template below is a verbatim clone of vireox-data-platform's
.github/pull_request_template.md — only the "### What" section and the Data Governance
checkbox are filled in; everything else is left exactly as the template provides it.
"""
from dataclasses import dataclass

from agent.access_control.configs import AccessControlConfigs
from agent.forms.access_request_submission import VerifiedAccessRequest


@dataclass
class PrEntry:
    """One approved request's contribution to the PR (a PR covers every request sharing a ticket_id)."""

    request: VerifiedAccessRequest
    requester_id: str | None
    is_new: bool


def _describe(entry: PrEntry) -> str:
    v = entry.request
    status = "New" if entry.is_new else "Existing"
    onboarding = [f"groups {', '.join(f'`{g}`' for g in v.groups)}"] if v.groups else []
    if v.tags:
        onboarding.append(f"tags {', '.join(f'`{t}`' for t in v.tags)}")
    parts = []
    if onboarding:
        parts.append(f"on-boarding ({'; '.join(onboarding)})")
    if v.filter_column and v.allowed_value:
        scope = f" within `{v.scope_column}` = `{v.scope_value}`" if v.scope_column and v.scope_value else ""
        parts.append(f"data access (`{v.filter_column}` = `{v.allowed_value}`{scope})")
    needs = " and ".join(parts) or "access"
    return f"- **{status}** `{v.user_email}` ({v.principal_type}) — {needs}; requested by user `{entry.requester_id}`"


def build_pr(ticket_id: str, entries: list[PrEntry]) -> tuple[str, str]:
    """Build the PR title and body covering every approved request that shares a ticket_id.

    Args:
        ticket_id: The governance ticket id shared by all entries (one branch/PR per ticket).
        entries: Every approved request on this ticket so far, including the one just approved.

    Returns:
        (title, body) — body is the cloned PR template with "### What" consolidated across all
        entries and the Data Governance checkbox ticked; every other section is left untouched.
    """
    title = f"govern: {ticket_id} Add/ update GPT user(s)"

    # One line per user: a later request for the same email on this ticket replaces the earlier one.
    entries = list({e.request.user_email: e for e in entries}.values())
    new_count = sum(e.is_new for e in entries)
    existing_count = len(entries) - new_count
    summary = " and ".join(
        label for label in (f"{new_count} new" if new_count else "", f"{existing_count} existing" if existing_count else "") if label
    )
    what_section = (
        "### What\n"
        f"{summary} GPT user(s) requested via Slack for on-boarding and/or data access under ticket `{ticket_id}`:\n\n"
        + "\n".join(_describe(e) for e in entries)
    )

    body = AccessControlConfigs.PR_TEMPLATE.replace(AccessControlConfigs.WHAT_PLACEHOLDER, what_section)
    body = body.replace(AccessControlConfigs.GOVERNANCE_CHECKBOX, AccessControlConfigs.GOVERNANCE_CHECKBOX.replace("[ ]", "[x]", 1))

    return title, body
