import pytest

from agent.pr_reminders.pr_links import PrRef
from agent.pr_reminders.queries import GithubQueryError, build_query, index_response

A1, A2, B7 = PrRef("org", "a", 1), PrRef("org", "a", 2), PrRef("org", "b", 7)


def test_build_query_groups_prs_by_repository():
    query = build_query([A1, B7, A2])

    assert query.count("repository(") == 2
    assert 'r0: repository(owner: "org", name: "a")' in query
    assert 'r1: repository(owner: "org", name: "b")' in query
    assert "p1: pullRequest(number: 1)" in query
    assert "p2: pullRequest(number: 2)" in query


def test_index_response_maps_each_pr_to_its_node():
    payload = {"data": {"r0": {"p1": {"title": "one"}, "p2": {"title": "two"}}, "r1": {"p7": {"title": "seven"}}}}

    nodes = index_response([A1, B7, A2], payload)

    assert nodes == {A1: {"title": "one"}, A2: {"title": "two"}, B7: {"title": "seven"}}


def test_index_response_maps_an_unreadable_repo_and_a_missing_pr_to_none():
    payload = {"data": {"r0": None, "r1": {"p7": None}}, "errors": [{"type": "NOT_FOUND"}]}

    nodes = index_response([A1, B7], payload)

    assert nodes == {A1: None, B7: None}


@pytest.mark.parametrize("payload", [{"errors": [{"message": "bad query"}]}, {"data": None, "errors": []}], ids=["no-data", "null-data"])
def test_index_response_raises_when_github_returned_no_data(payload):
    with pytest.raises(GithubQueryError, match="GraphQL query failed"):
        index_response([A1], payload)
