"""Fills the data-platform repo's PR template for an approved access request.

The template below is a verbatim clone of vireox-data-platform's
.github/pull_request_template.md — only the "### What" section and the Data Governance
checkbox are filled in; everything else is left exactly as the template provides it.
"""
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


def build_pr(v: VerifiedAccessRequest, requester_id: str | None, approver_id: str | None) -> tuple[str, str]:
    """Build the PR title and body for an approved access request.

    Args:
        v: The verified, approved access request (with the final resolved principal).
        requester_id: Slack user ID who submitted the original request.
        approver_id: Slack user ID who approved it.

    Returns:
        (title, body) — body is the cloned PR template with "### What" filled in and the
        Data Governance checkbox ticked; every other section is left untouched.
    """
    title = f"govern: {v.ticket_id} Add user {v.user_email}"

    record = "\n".join(
        [
            f"ticket_id: {v.ticket_id}",
            f"display_name: {v.display_name}",
            f"user_email: {v.user_email}",
            f"principal: {v.principal}",
            f"filter_column: {v.filter_column}",
            f"allowed_value: {v.allowed_value}",
            f"scope_column: {v.scope_column}",
            f"scope_value: {v.scope_value}",
            f"principal_type: {v.principal_type}",
            f"groups: [{', '.join(v.groups)}]",
            f"tags: [{', '.join(v.tags)}]",
        ]
    )
    what_section = (
        "### What\n"
        f"Adds a new row filter access rule for `{v.user_email}`, requested via Slack by "
        f"user `{requester_id}` and approved by user `{approver_id}`.\n\n"
        f"```\n{record}\n```"
    )

    body = _TEMPLATE.replace(_WHAT_PLACEHOLDER, what_section)
    body = body.replace(_GOVERNANCE_CHECKBOX, _GOVERNANCE_CHECKBOX.replace("[ ]", "[x]", 1))

    return title, body
