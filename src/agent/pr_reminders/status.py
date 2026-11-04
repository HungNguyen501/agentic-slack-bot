"""Decide whether a pull request is waiting for review, from the node `queries` fetched. Pure, no I/O."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

from agent.pr_reminders.pr_links import PrLink


class Bucket(Enum):
    WAITING = auto()  # open, needs a review
    WAITING_ON_AUTHOR = auto()  # changes requested
    APPROVED = auto()
    DONE = auto()  # merged or closed
    DRAFT = auto()
    INACCESSIBLE = auto()  # GitHub returned null: the credentials can't see the repo, or the PR doesn't exist


@dataclass(frozen=True)
class PrStatus:
    link: PrLink
    bucket: Bucket
    title: str
    created_at: datetime | None


def _decision_from_reviews(pr: dict) -> str:
    reviews = pr["latestOpinionatedReviews"]
    states = {review["state"] for review in reviews["nodes"]}
    if "CHANGES_REQUESTED" in states:
        return "CHANGES_REQUESTED"
    if reviews["totalCount"] > len(reviews["nodes"]):
        # Only the first page of reviews was fetched: a hidden "changes requested" is possible, so don't call it approved.
        return "REVIEW_REQUIRED"
    if "APPROVED" in states:
        return "APPROVED"
    return "REVIEW_REQUIRED"


def classify(pr: dict | None) -> Bucket:
    """Bucket one PR node the way GitHub's Merge button would.

    `reviewDecision` folds in branch protection and required reviewer counts. It is null when the repo has no
    required-review rule, so then fall back to each reviewer's latest review.
    """
    if pr is None:
        return Bucket.INACCESSIBLE
    if pr["state"] != "OPEN":
        return Bucket.DONE
    if pr["isDraft"]:
        return Bucket.DRAFT

    decision = pr["reviewDecision"] or _decision_from_reviews(pr)
    return {"APPROVED": Bucket.APPROVED, "CHANGES_REQUESTED": Bucket.WAITING_ON_AUTHOR}.get(decision, Bucket.WAITING)


def build_status(link: PrLink, pr: dict | None) -> PrStatus:
    """Combine a link with its fetched node; an inaccessible PR keeps only its link."""
    created_at = datetime.fromisoformat(pr["createdAt"]) if pr else None
    return PrStatus(link, classify(pr), pr["title"] if pr else link.ref.slug, created_at)
