"""GitHub REST API client for opening the data-platform access-control PR."""
import base64
import re

import httpx

from common.configs import Configs, GithubConfigs


def _headers() -> dict:
    return {"Authorization": f"Bearer {Configs.GIT_REPO_PAT_DATA_PLATFORM}", "Accept": "application/vnd.github+json"}


def _get_file(path: str, ref: str) -> tuple[str, str]:
    """Fetch a file's decoded content and blob sha at a given ref (branch, tag, or commit)."""
    resp = httpx.get(f"{GithubConfigs.API}/contents/{path}", headers=_headers(), params={"ref": ref}, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def _ensure_branch(branch: str) -> None:
    """Create `branch` off the base branch's current commit if it doesn't already exist."""
    check = httpx.get(f"{GithubConfigs.API}/git/ref/heads/{branch}", headers=_headers(), timeout=30.0)
    if check.status_code == 200:
        return

    base = httpx.get(f"{GithubConfigs.API}/git/ref/heads/{GithubConfigs.BASE_BRANCH}", headers=_headers(), timeout=30.0)
    base.raise_for_status()
    base_sha = base.json()["object"]["sha"]

    resp = httpx.post(
        f"{GithubConfigs.API}/git/refs",
        headers=_headers(),
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        timeout=30.0,
    )
    resp.raise_for_status()


def _update_file(path: str, branch: str, content: str, message: str) -> None:
    """Create or overwrite a file on `branch` with `content` (a single commit on that branch)."""
    try:
        _, sha = _get_file(path, branch)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        sha = None

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    resp = httpx.put(f"{GithubConfigs.API}/contents/{path}", headers=_headers(), json=payload, timeout=30.0)
    resp.raise_for_status()


def _branch_name(ticket_id: str) -> str:
    """Build a git-ref-safe branch name from a free-text ticket id."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ticket_id.strip()).strip("-.") or "unspecified"
    return f"govern/{safe}"


def _find_open_pull_request(branch: str) -> str | None:
    """Return the html_url of an already-open PR for this branch, if one exists."""
    resp = httpx.get(
        f"{GithubConfigs.API}/pulls",
        headers=_headers(),
        params={"head": f"{GithubConfigs.OWNER}:{branch}", "state": "open"},
        timeout=30.0,
    )
    resp.raise_for_status()
    prs = resp.json()
    return prs[0]["html_url"] if prs else None


def open_data_access_pr(
    ticket_id: str,
    rules_entry_text: str,
    service_principals_yaml: str | None,
    pr_title: str,
    pr_body: str,
) -> str:
    """Append a row-filter rule (and optionally replace the SP audit snapshot) and open a PR.

    Multiple approved requests can share a ticket_id (hence the same branch/PR) — e.g. one
    ticket covering access for two different users — so the base content to append onto is
    always read from the *branch* (which already reflects any earlier requests' appends),
    never from `main`. A freshly created branch is itself an exact copy of main, so this is
    never missing the file. Retry-safety for re-approving the *same* request comes from
    checking whether its exact entry text is already present before appending again — RQ
    retrying this call, or a reviewer re-sending "approve", can't produce a duplicate line.
    Branch creation and PR creation are both check-then-create.

    Args:
        ticket_id: The governance ticket id — determines the branch name
            ("govern/<ticket_id>", sanitized for git ref safety) and each commit message
            ("govern: <ticket_id> <content of changes>").
        rules_entry_text: The new rules_v2.yaml entry, as a fully-formed text block to append.
        service_principals_yaml: Full replacement content for service_principals.yaml, or None
            to leave that file untouched (the "Users" principal_type path never touches it).
        pr_title: Pull request title.
        pr_body: Pull request body (already filled from the PR template).

    Returns:
        The opened (or already-open) pull request's html_url.
    """
    branch = _branch_name(ticket_id)
    _ensure_branch(branch)

    rules_path = "core/platform/configs/prod/access_control/rules_v2.yaml"
    current_rules_content, _ = _get_file(rules_path, branch)
    if rules_entry_text not in current_rules_content:
        if not current_rules_content.endswith("\n"):
            current_rules_content += "\n"
        _update_file(
            rules_path,
            branch,
            current_rules_content + rules_entry_text,
            f"govern: {ticket_id} Add row filter access rule",
        )

    if service_principals_yaml is not None:
        _update_file(
            "core/platform/configs/prod/access_control/service_principals.yaml",
            branch,
            service_principals_yaml,
            f"govern: {ticket_id} Update service principals audit snapshot",
        )

    existing = _find_open_pull_request(branch)
    if existing:
        return existing

    resp = httpx.post(
        f"{GithubConfigs.API}/pulls",
        headers=_headers(),
        json={"title": pr_title, "head": branch, "base": GithubConfigs.BASE_BRANCH, "body": pr_body},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["html_url"]
