"""Fills the data-platform repo's PR template for an approved access request.

The template below is a verbatim clone of vireox-data-platform's
.github/pull_request_template.md — only the "### What" section and the Data Governance
checkbox are filled in; everything else is left exactly as the template provides it.
"""
from dataclasses import dataclass

from .access_request_submission import VerifiedAccessRequest

# Built as explicit lines (rather than one raw triple-quoted block) so the two lines that end
# in a literal trailing space in the source template don't trip our own trailing-whitespace lint.
_TEMPLATE_LINES = [
    "> ⚠️ **If any part of this PR was written with AI/agent assistance**: review and",
    "> refactor it yourself first — dead code, leftover comments, overly defensive",
    "> error handling, unnecessary abstractions, inconsistent naming — before requesting",
    "> review. Don't push AI-generated slop and make a human clean it up for you.",
    "",
    "## Summary",
    "<!--- Add some bells and whistles for PR template. --->",
    "",
    "### Why",
    "<!--- Clearly define the issue or problem that your changes address. ",
    "Describe what is currently not working as expected or what feature is missing. --->",
    "",
    "### What",
    "<!--- Provide a high-level overview of what has been modified, added, or removed in the codebase. ",
    "This could include new features, bug fixes, refactoring efforts, or performance optimizations. --->",
    "",
    "### Solution",
    "<!--- Describe the architectural or design decisions you made while implementing the changes.",
    "Explain the thought process behind your approach and how it aligns with best practices or existing patterns in the codebase. --->",
    "",
    "## Types of Changes",
    "<!--- What types of changes does your code introduce? Put an `x` in all the boxes that apply --->",
    "- [ ] ❌ Breaking change (fix or feature that would cause existing functionality to not work as expected)",
    "- [ ] \U0001f680 New feature (non-breaking change which adds functionality)",
    "- [ ] \U0001f577 Bug fix (non-breaking change which fixes an issue)",
    "- [ ] \U0001f44f Performance optimization (non-breaking change which addresses a performance issue)",
    "- [ ] \U0001f6e0 Refactor (non-breaking change which does not change existing behavior or add new functionality)",
    "- [ ] \U0001f527 Chore (routine maintenance or tasks not affecting users)",
    "- [ ] \U0001f4d7 Library update (non-breaking change that will update one or more libraries to newer versions)",
    "- [ ] \U0001f4e6 Build (build system or external dependencies changes)",
    "- [ ] ⚙️ CI (CI/CD configuration and scripts)",
    "- [ ] \U0001f3d7 Infrastructure (infrastructure-related changes)",
    "- [ ] \U0001f5c2 Data governance (changes to data access, ownership, classification, or compliance)",
    "- [ ] ✅ Test (non-breaking change related to testing)",
    "- [ ] \U0001f4dd Documentation (non-breaking change that doesn't change code behavior, can skip testing)",
    "- [ ] ⏪ Revert (reverts a previous change)",
    "",
    "## Test Plan",
    "<!--- Please input steps on how to test this PR, including evidence in the form of captured images or videos. If this is not necessary, provide the reason why. --->",  # noqa: E501
    "",
    "## Related Issues",
    "<!---Add a reference section for management tickets, and relevant conversations.--->",
    "",
]
_TEMPLATE = "\n".join(_TEMPLATE_LINES)

_WHAT_PLACEHOLDER = (
    "### What\n"
    "<!--- Provide a high-level overview of what has been modified, added, or removed in the codebase. \n"
    "This could include new features, bug fixes, refactoring efforts, or performance optimizations. --->"
)
_GOVERNANCE_CHECKBOX = "- [ ] \U0001f5c2 Data governance (changes to data access, ownership, classification, or compliance)"


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

    body = _TEMPLATE.replace(_WHAT_PLACEHOLDER, what_section)
    body = body.replace(_GOVERNANCE_CHECKBOX, _GOVERNANCE_CHECKBOX.replace("[ ]", "[x]", 1))

    return title, body
