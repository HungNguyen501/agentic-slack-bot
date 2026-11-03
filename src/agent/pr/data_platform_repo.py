"""Access-control edits to the data-platform repo: rules_v2.yaml / service_principals.yaml on a per-ticket branch and PR."""
import re

from common.configs import GithubConfigs
from connectors import github

_RULES_PATH = "core/platform/configs/prod/access_control/rules_v2.yaml"
_SERVICE_PRINCIPALS_PATH = "core/platform/configs/prod/access_control/service_principals.yaml"


def _branch_name(ticket_id: str) -> str:
    """Build a git-ref-safe branch name from a free-text ticket id."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ticket_id.strip()).strip("-.") or "unspecified"
    return f"govern/{safe}"


def base_rule_emails() -> set[str]:
    """Return every user_email already present in rules_v2.yaml on the base branch (i.e. existing users)."""
    content, _ = github.get_file(_RULES_PATH, GithubConfigs.BASE_BRANCH)
    return set(re.findall(r"^\s*(?:-\s+)?user_email:\s*(\S+)", content, flags=re.MULTILINE))


def open_data_access_pr(
    ticket_id: str,
    user_email: str,
    rules_entry_text: str,
    service_principals_yaml: str | None,
    pr_title: str,
    pr_body: str,
) -> str:
    """Append a row-filter rule (and optionally replace the SP audit snapshot) and open a PR.

    Multiple approved requests can share a ticket_id (hence the same branch/PR) — e.g. one
    ticket covering access for two different users — each lands as its own commit (tagged with
    its user_email) and the PR's title/body are refreshed to cover all of them. So the base content to append onto is
    always read from the *branch* (which already reflects any earlier requests' appends),
    never from `main`. A freshly created branch is itself an exact copy of main, so this is
    never missing the file. Retry-safety for re-approving the *same* request comes from
    checking whether its exact entry text is already present before appending again — RQ
    retrying this call, or a reviewer re-sending "approve", can't produce a duplicate line.
    Branch creation and PR creation are both check-then-create.

    Args:
        ticket_id: The governance ticket id — determines the branch name
            ("govern/<ticket_id>", sanitized for git ref safety).
        user_email: The user this call's commits are for; included in each commit message
            ("govern: <ticket_id> <content of changes> <user_email>") to identify the update.
        rules_entry_text: The new rules_v2.yaml entry, as a fully-formed text block to append.
        service_principals_yaml: Full replacement content for service_principals.yaml, or None
            to leave that file untouched (the "Users" principal_type path never touches it).
        pr_title: Pull request title.
        pr_body: Pull request body (already filled from the PR template, consolidated across
            every approved request on the ticket). Overwrites an already-open PR's body.

    Returns:
        The opened (or already-open) pull request's html_url.
    """
    branch = _branch_name(ticket_id)
    github.ensure_branch(branch)

    current_rules_content, _ = github.get_file(_RULES_PATH, branch)
    if rules_entry_text not in current_rules_content:
        if not current_rules_content.endswith("\n"):
            current_rules_content += "\n"
        github.update_file(
            _RULES_PATH,
            branch,
            current_rules_content + rules_entry_text,
            f"govern: {ticket_id} Add/ update row filter access rule for {user_email}",
        )

    if service_principals_yaml is not None:
        github.update_file(
            _SERVICE_PRINCIPALS_PATH,
            branch,
            service_principals_yaml,
            f"govern: {ticket_id} Update service principals audit snapshot for {user_email}",
        )

    existing = github.find_open_pull_request(branch)
    if existing:
        url, number = existing
        github.update_pull_request(number, pr_title, pr_body)
        return url

    return github.create_pull_request(pr_title, branch, pr_body)
