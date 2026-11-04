"""Find GitHub pull request links in Slack messages and work out which thread each one lives in. Pure, no I/O."""
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

_PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

# GraphQL's Int is 32-bit: one larger number makes GitHub reject the whole query, failing every PR in the batch.
_MAX_PR_NUMBER = 2**31 - 1
_MAX_PR_NUMBER_DIGITS = len(str(_MAX_PR_NUMBER))


@dataclass(frozen=True)
class PrRef:
    owner: str
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass(frozen=True)
class PrLink:
    """A PR, the Slack message that mentions it, and the thread a reminder for it goes to."""

    ref: PrRef
    channel: str
    thread_ts: str
    message_ts: str


def _pr_number(digits: str) -> int | None:
    """The PR number in `digits`, or None if it can't be one. The length is checked before `int()`, which raises on
    absurdly long digit strings (a Slack message can hold thousands of digits)."""
    significant = digits.lstrip("0")
    if not significant or len(significant) > _MAX_PR_NUMBER_DIGITS:
        return None
    number = int(significant)
    return number if number <= _MAX_PR_NUMBER else None


def find_pr_refs(text: str) -> list[PrRef]:
    """Return the distinct PRs linked in `text`, in order of first appearance.

    Owner and repo are lower-cased, since GitHub ignores case, so one PR linked two ways counts once. Numbers too
    large to be a PR are ignored.
    """
    refs = {}
    for owner, repo, digits in _PR_URL_RE.findall(text):
        number = _pr_number(digits)
        if number is not None:
            refs[PrRef(owner.lower(), repo.lower(), number)] = None
    return list(refs)


def _message_blob(message: dict) -> str:
    # The GitHub Slack app posts PRs as attachments with an empty `text`, and rich-text blocks can hold links `text` lacks.
    return " ".join([message.get("text") or "", json.dumps(message.get("attachments") or []), json.dumps(message.get("blocks") or [])])


def links_from_messages(messages: Iterable[dict], channel: str, *, own_app_id: str | None) -> list[PrLink]:
    """Collect PR links from Slack messages, keeping the most recent message for a PR linked more than once.

    The reminder thread is the message's own thread, or the thread that replying to it starts. Messages from
    `own_app_id` (this bot) are skipped: its reminders repeat the PR links and would keep old PRs in the window.
    """
    latest: dict[PrRef, PrLink] = {}
    for message in messages:
        if own_app_id and message.get("app_id") == own_app_id:
            continue
        ts = message["ts"]
        for ref in find_pr_refs(_message_blob(message)):
            current = latest.get(ref)
            if current is None or float(ts) > float(current.message_ts):
                latest[ref] = PrLink(ref, channel, message.get("thread_ts") or ts, ts)
    return list(latest.values())


def links_from_urls(urls: Iterable[str], channel: str, thread_ts: str) -> list[PrLink]:
    """Build links for PR URLs typed in the request itself; they live in the request's own thread."""
    return [PrLink(ref, channel, thread_ts, thread_ts) for ref in find_pr_refs(" ".join(urls))]


def merge_links(base: Iterable[PrLink], overrides: Iterable[PrLink]) -> list[PrLink]:
    """Combine two link lists into one per PR; a PR in `overrides` replaces the same PR in `base`."""
    return list({link.ref: link for link in [*base, *overrides]}.values())


def limit_links(links: list[PrLink], limit: int) -> tuple[list[PrLink], int]:
    """Keep the `limit` most recently mentioned PRs; also return how many were dropped."""
    ordered = sorted(links, key=lambda link: float(link.message_ts), reverse=True)
    return ordered[:limit], max(0, len(ordered) - limit)
