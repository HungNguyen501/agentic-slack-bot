from datetime import UTC, datetime

import pytest

from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.status import Bucket, build_status, classify

LINK = PrLink(PrRef("org", "repo", 1), "C1", "1.0", "1.0")


def test_classify_marks_a_pr_the_token_cannot_see_as_inaccessible():
    assert classify(None) is Bucket.INACCESSIBLE


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"state": "MERGED"}, Bucket.DONE),
        ({"state": "CLOSED"}, Bucket.DONE),
        ({"is_draft": True}, Bucket.DRAFT),
        ({"decision": "APPROVED"}, Bucket.APPROVED),
        ({"decision": "CHANGES_REQUESTED"}, Bucket.WAITING_ON_AUTHOR),
        ({"decision": "REVIEW_REQUIRED"}, Bucket.WAITING),
    ],
    ids=["merged", "closed", "draft", "approved", "changes-requested", "review-required"],
)
def test_classify_follows_github_review_decision(pr_node, fields, expected):
    assert classify(pr_node(**fields)) is expected


@pytest.mark.parametrize(
    "reviews, expected",
    [
        ((), Bucket.WAITING),
        (("COMMENTED",), Bucket.WAITING),
        (("APPROVED",), Bucket.APPROVED),
        (("APPROVED", "APPROVED"), Bucket.APPROVED),
        (("CHANGES_REQUESTED",), Bucket.WAITING_ON_AUTHOR),
        (("APPROVED", "CHANGES_REQUESTED"), Bucket.WAITING_ON_AUTHOR),
    ],
    ids=["no-reviews", "comment-only", "one-approval", "two-approvals", "changes-requested", "mixed"],
)
def test_classify_falls_back_to_latest_reviews_when_repo_has_no_required_review_rule(pr_node, reviews, expected):
    assert classify(pr_node(decision=None, reviews=reviews)) is expected


def test_classify_ignores_reviews_when_github_gives_a_decision(pr_node):
    assert classify(pr_node(decision="REVIEW_REQUIRED", reviews=("APPROVED",))) is Bucket.WAITING


def test_build_status_keeps_title_and_creation_time(pr_node):
    status = build_status(LINK, pr_node())

    assert status.title == "Fix it"
    assert status.created_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert status.bucket is Bucket.WAITING


def test_build_status_for_an_inaccessible_pr_keeps_only_the_link():
    status = build_status(LINK, None)

    assert status.bucket is Bucket.INACCESSIBLE
    assert status.title == "org/repo#1"
    assert status.created_at is None
