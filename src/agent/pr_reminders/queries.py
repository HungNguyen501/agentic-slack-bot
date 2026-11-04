"""GitHub GraphQL query for PR approval state, and the matching response parser. Pure, no I/O."""
import json

from agent.pr_reminders.pr_links import PrRef

_PR_FIELDS = "url title state isDraft reviewDecision createdAt latestOpinionatedReviews(first: 20) { totalCount nodes { state } }"


class GithubQueryError(RuntimeError):
    """GitHub answered the GraphQL query with errors and no data at all."""


def _group_by_repo(refs: list[PrRef]) -> dict[tuple[str, str], list[int]]:
    by_repo: dict[tuple[str, str], list[int]] = {}
    for ref in refs:
        by_repo.setdefault((ref.owner, ref.repo), []).append(ref.number)
    return by_repo


def build_query(refs: list[PrRef]) -> str:
    """Build one query that fetches every PR, aliasing repositories as r0, r1, … and PRs as p<number>."""
    repos = []
    for index, ((owner, repo), numbers) in enumerate(_group_by_repo(refs).items()):
        prs = " ".join(f"p{number}: pullRequest(number: {number}) {{ {_PR_FIELDS} }}" for number in numbers)
        # json.dumps yields a valid GraphQL string, and pr_links only lets [\w.-] through anyway.
        repos.append(f"r{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{ {prs} }}")
    return "query { " + " ".join(repos) + " }"


def index_response(refs: list[PrRef], payload: dict) -> dict[PrRef, dict | None]:
    """Map each requested PR to its GraphQL node, or None when GitHub returned null for it.

    Raises:
        GithubQueryError: the payload has no `data`, e.g. the query itself was rejected.
    """
    data = payload.get("data")
    if data is None:
        raise GithubQueryError(f"GitHub GraphQL query failed: {payload.get('errors')}")
    nodes: dict[PrRef, dict | None] = {}
    for index, ((owner, repo), numbers) in enumerate(_group_by_repo(refs).items()):
        repository = data.get(f"r{index}") or {}
        for number in numbers:
            nodes[PrRef(owner, repo, number)] = repository.get(f"p{number}")
    return nodes
