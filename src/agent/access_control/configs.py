import re

from common.configs import AgentConfigs


class AccessControlConfigs:
    """Every constant of the data access flow: form and Slack limits, the repo paths and PR template of the access-control PR, and the
    patterns and wording it matches and posts."""

    # Matches a UUID anywhere after the leading approve/reject keyword, so phrasing like
    # "approve data access request ID=<uuid>" or "approve: <uuid>" works, not just the exact
    # `approve <uuid>` form we ask for in the review message.
    REQUEST_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

    ACCESS_REQUEST_POSTED_MESSAGE = (
        "Click below to fill out a data access request. "
        f"This link expires in {int(AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS / 60)} minutes."
    )

    # Names of the synced_datasets (see src/dataset_sync/) the form reads.
    LOCATIONS_DATASET = "store_locations"
    TAGS_DATASET = "row_filter_tags"

    # Slack's limits on select menus: 100 options per select and per option group, 100 option groups, 150 characters in an
    # option's value (the part submitted, which identifies the access requested, so it is never shortened) and 75 in a group label.
    SLACK_MAX_OPTIONS = 100
    SLACK_MAX_GROUPS = 100
    SLACK_MAX_VALUE_LENGTH = 150
    SLACK_MAX_LABEL_LENGTH = 75

    # Slack allows 75 characters of option text and of an option group's label.
    OPTION_TEXT_MAX = 75

    EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    # Built as explicit lines (rather than one raw triple-quoted block) so the two lines that end
    # in a literal trailing space in the source template don't trip our own trailing-whitespace lint.
    PR_TEMPLATE_LINES = [
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
    PR_TEMPLATE = "\n".join(PR_TEMPLATE_LINES)

    WHAT_PLACEHOLDER = (
        "### What\n"
        "<!--- Provide a high-level overview of what has been modified, added, or removed in the codebase. \n"
        "This could include new features, bug fixes, refactoring efforts, or performance optimizations. --->"
    )
    GOVERNANCE_CHECKBOX = "- [ ] \U0001f5c2 Data governance (changes to data access, ownership, classification, or compliance)"

    RULES_PATH = "core/platform/configs/prod/access_control/rules_v2.yaml"
    SERVICE_PRINCIPALS_PATH = "core/platform/configs/prod/access_control/service_principals.yaml"
