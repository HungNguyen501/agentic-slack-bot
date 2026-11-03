import inspect
from dataclasses import asdict

from agent.access_control.access_requests import add_access_request
from agent.access_control.submission import _resolve_principal, _review_message, _to_verified


def test_verified_fields_are_accepted_by_add_access_request():
    verified = _to_verified({"filter_column": "c", "groups": ["g"]}, "T-1", "Users", "a@b.com", "A", "a@b.com")

    accepted = set(inspect.signature(add_access_request).parameters)

    assert set(asdict(verified)) <= accepted


def test_to_verified_defaults_missing_form_fields():
    verified = _to_verified({}, "T-1", "Users", "a@b.com", "A", "a@b.com")

    assert (verified.filter_column, verified.groups, verified.tags) == ("", [], [])


def test_resolve_principal_service_principal_not_found_is_expected_not_missing():
    assert _resolve_principal("a@b.com", "Service principals", None) == ("svc-a@b.com", "(created on approval)", False)


def test_resolve_principal_user_not_found_is_flagged_missing():
    assert _resolve_principal("a@b.com", "Users", None) == ("a@b.com", "(does not exist)", True)


def test_review_message_counts_sections_and_mentions_reviewers():
    message = _review_message("T-1", "U1", ["U2", "U3"], ["one", "two"])

    assert "2 sub-request(s) for ticket `T-1`" in message
    assert "<@U1>" in message
    assert message.endswith("Please help review: <@U2>, <@U3>")


def test_review_message_falls_back_to_data_team_without_reviewers():
    assert _review_message("T-1", "U1", [], ["one"]).endswith("Please help review: the data team")
