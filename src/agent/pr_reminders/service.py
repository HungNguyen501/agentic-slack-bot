"""PR review reminders: find PR links in Slack, check them on GitHub, and remind in the thread of each PR still waiting."""
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NamedTuple

from slack_sdk.errors import SlackApiError, SlackClientError

from agent.pr_reminders import message, queries
from agent.pr_reminders.limits import is_reminder_expired, is_within_active_hours
from agent.pr_reminders.pr_links import PrLink, limit_links, links_from_messages, links_from_urls, merge_links
from agent.pr_reminders.status import Bucket, PrStatus, build_status
from common.configs import AgentConfigs, ReminderConfig
from connectors import github, slack
from connectors.db.bots import BotConfig
from connectors.db.schedules import get_schedule, set_schedule_status

log = logging.getLogger("agent.pr_reminders")

# Slack errors meaning "this bot can't read that channel" (retrying won't fix them), and what to tell the user.
CHANNEL_READ_HELP = {
    "not_in_channel": "I'm not a member of that channel, so I can't read it. Invite me and try again.",
    "channel_not_found": "I can't find that channel.",
    "missing_scope": "My Slack app is missing the channels:history / groups:history scopes. An admin needs to add them and reinstall the app.",
    "is_archived": "That channel is archived.",
}

# Slack allows about one message per second per channel, and a run can post into many threads of one channel.
_POST_GAP_SECONDS = 1.0

# A 429's Retry-After can be long; waiting it all out would hold the worker and run into the job timeout.
_MAX_RETRY_AFTER_SECONDS = 30.0


class ChannelReadError(Exception):
    """The bot can't read the channel it was asked to scan (not a member, missing history scope, …)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class CheckResult:
    """Outcome of a check. `truncated` PRs were never checked and `deferred_threads` had waiting PRs but no message this run."""

    statuses: list[PrStatus]
    truncated: int = 0
    posted: list[tuple[str, str]] = field(default_factory=list)
    failed_posts: int = 0
    deferred_threads: int = 0

    def count(self, bucket: Bucket) -> int:
        return sum(1 for status in self.statuses if status.bucket is bucket)

    @property
    def waiting(self) -> int:
        return self.count(Bucket.WAITING)


def collect_links(
    bot: BotConfig,
    *,
    scope: str,
    channel: str,
    thread_ts: str,
    lookback_hours: int | None,
    typed_urls: list[str] | None = None,
) -> list[PrLink]:
    """Read Slack and return the PRs linked in the thread `thread_ts` (scope "thread") or in the channel's recent messages (scope "channel").

    `typed_urls` are PR links typed in the request itself; they live in `thread_ts` and win over the same PR found in a message.

    Raises:
        ChannelReadError: the bot can't read the channel.
    """
    try:
        if scope == "thread":
            messages = slack.conversations_replies(channel, thread_ts, token=bot.bot_token)
        else:
            messages = slack.conversations_history(
                channel, time.time() - lookback_hours * 3600, token=bot.bot_token, max_messages=AgentConfigs.PR_REMINDER_MAX_MESSAGES_SCANNED
            )
    except SlackApiError as exc:
        code = exc.response.get("error") if exc.response is not None else None
        if code in CHANNEL_READ_HELP:
            raise ChannelReadError(code) from exc
        raise

    scanned = links_from_messages(messages, channel, own_app_id=bot.app_id)
    return merge_links(scanned, links_from_urls(typed_urls or [], channel, thread_ts))


def fetch_statuses(links: list[PrLink]) -> list[PrStatus]:
    """Ask GitHub for the state of every linked PR in one query."""
    if not links:
        return []
    refs = [link.ref for link in links]
    nodes = queries.index_response(refs, github.graphql(queries.build_query(refs)))
    return [build_status(link, nodes.get(link.ref)) for link in links]


def _post(bot: BotConfig, channel: str, thread_ts: str, text: str) -> bool:
    """Post `text` as replies in a thread, backing off once on a 429. Returns False (and logs) when it couldn't be posted."""
    for chunk in slack.split_message(text):
        for attempt in range(2):
            try:
                slack.post_message(channel, chunk, thread_ts, token=bot.bot_token)
                break
            except SlackApiError as exc:
                rate_limited = exc.response is not None and exc.response.status_code == 429
                if rate_limited and attempt == 0:
                    time.sleep(min(float(exc.response.headers.get("Retry-After", 1)), _MAX_RETRY_AFTER_SECONDS))
                    continue
                log.exception("Could not post a PR reminder to %s (thread %s)", channel, thread_ts)
                return False
            except (SlackClientError, OSError):
                # A timeout or dropped connection. Raising would make RQ retry the job and repeat @channel in the
                # threads already posted to, so count it as a failed post; the next tick covers this thread.
                log.exception("Could not reach Slack to post a PR reminder to %s (thread %s)", channel, thread_ts)
                return False
    return True


class _ThreadReminder(NamedTuple):
    channel: str
    thread_ts: str
    waiting: list[PrStatus]
    on_author: list[PrStatus]


def _threads_to_remind(statuses: list[PrStatus], include_author_only_threads: bool) -> list[_ThreadReminder]:
    """Group PRs by thread and keep the threads worth a message.

    Most overdue first: threads with a PR waiting for review before author-only ones, and within each the thread
    whose oldest PR has been open longest.
    """
    by_thread: dict[tuple[str, str], list[PrStatus]] = {}
    for status in statuses:
        by_thread.setdefault((status.link.channel, status.link.thread_ts), []).append(status)

    threads = []
    for (channel, thread_ts), group in by_thread.items():
        waiting = [s for s in group if s.bucket is Bucket.WAITING]
        on_author = [s for s in group if s.bucket is Bucket.WAITING_ON_AUTHOR]
        if waiting or (include_author_only_threads and on_author):
            threads.append(_ThreadReminder(channel, thread_ts, waiting, on_author))
    return sorted(threads, key=lambda thread: (not thread.waiting, min(s.created_at for s in thread.waiting or thread.on_author)))


def check_and_remind(
    bot: BotConfig,
    links: list[PrLink],
    *,
    schedule_id: str | None = None,
    include_author_only_threads: bool = False,
    now: datetime | None = None,
) -> CheckResult:
    """Check the PRs on GitHub and post one reminder in each thread that has a PR waiting for review.

    Only the PR_REMINDER_MAX_PRS_CHECKED most recently mentioned PRs are checked (the rest are counted in
    `truncated`, so a caller can't conclude "nothing is waiting"), and only the PR_REMINDER_MAX_THREADS_POSTED most
    overdue threads get a message (the rest are `deferred_threads`).

    `schedule_id` is set for a recurring reminder, so the message says how to stop it. `include_author_only_threads`
    also posts, without a ping, in threads whose only news is "changes requested": a one-off check wants that, a
    recurring one would repeat it every run.

    Raises:
        httpx.HTTPError, queries.GithubQueryError: GitHub couldn't be queried. Nothing is posted in that case.
    """
    links, truncated = limit_links(links, AgentConfigs.PR_REMINDER_MAX_PRS_CHECKED)
    statuses = fetch_statuses(links)
    now = now or datetime.now(UTC)

    threads = _threads_to_remind(statuses, include_author_only_threads)
    cap = AgentConfigs.PR_REMINDER_MAX_THREADS_POSTED
    result = CheckResult(statuses, truncated, deferred_threads=max(0, len(threads) - cap))
    for thread in threads[:cap]:
        if result.posted or result.failed_posts:
            time.sleep(_POST_GAP_SECONDS)
        text = message.render_thread_message(thread.waiting, thread.on_author, now=now, schedule_id=schedule_id)
        if _post(bot, thread.channel, thread.thread_ts, text):
            result.posted.append((thread.channel, thread.thread_ts))
        else:
            result.failed_posts += 1
    return result


def _end_reminder(bot: BotConfig, schedule_id: str, config: ReminderConfig, status: str, note: str) -> None:
    """Soft-delete the reminder, then say why in its origin thread. Ending first, so a Slack failure can't leave it firing."""
    set_schedule_status(schedule_id, status)
    _post(bot, config.origin_channel, config.origin_thread_ts, note)


def _nothing_left_to_remind(schedule_id: str, result: CheckResult) -> bool:
    """Whether this run proves no PR is waiting, so the reminder can end.

    Not seeing a waiting PR proves nothing if PRs were left unchecked (the cap) or none could be read from GitHub.
    """
    if result.waiting:
        return False
    if result.truncated:
        log.warning("PR reminder %s: %d PR(s) were left unchecked, so it stays active", schedule_id, result.truncated)
        return False
    if result.statuses and result.count(Bucket.INACCESSIBLE) == len(result.statuses):
        log.warning("PR reminder %s: none of its %d PR(s) could be read from GitHub, so it stays active", schedule_id, len(result.statuses))
        return False
    return True


def run_reminder(bot: BotConfig, schedule_id: str, now: datetime | None = None) -> None:
    """Run one tick of a recurring PR reminder: remind in the threads of PRs waiting for review, or end the reminder.

    A tick outside the active hours does nothing at all (no reads, no posts, reminder not ended), so it resumes in the
    morning. The reminder ends (soft delete) when `_nothing_left_to_remind`, when it has outlived its lifetime, when its
    config is malformed, or when the bot can no longer read the channel. A transient GitHub or Slack read failure
    propagates, so RQ retries the job and the reminder stays active.
    """
    now = now or datetime.now(UTC)
    if not is_within_active_hours(now):
        log.info("PR reminder %s: outside active hours — skipping this tick", schedule_id)
        return

    schedule = get_schedule(schedule_id)
    if schedule is None or schedule.kind != "pr_reminder" or schedule.status != "active":
        log.info("PR reminder %s is gone or no longer active — skipping", schedule_id)
        return

    try:
        config = ReminderConfig.from_dict(schedule.config)
    except ValueError:
        log.exception("PR reminder %s has a malformed config — ending it", schedule_id)
        set_schedule_status(schedule_id, "completed")
        return

    if is_reminder_expired(schedule.created_at, now):
        _end_reminder(bot, schedule_id, config, "expired", message.render_expired_note(schedule_id, AgentConfigs.PR_REMINDER_LIFETIME_DAYS))
        return

    try:
        links = collect_links(
            bot, scope=config.scope, channel=schedule.channel, thread_ts=config.origin_thread_ts, lookback_hours=config.lookback_hours
        )
    except ChannelReadError as exc:
        log.warning("PR reminder %s can't read %s (%s) — ending it", schedule_id, schedule.channel, exc.code)
        _end_reminder(bot, schedule_id, config, "completed", message.render_unreadable_note(schedule_id, exc.code))
        return

    result = check_and_remind(bot, links, schedule_id=schedule_id, now=now)
    log.info("PR reminder %s: %d PR(s) checked, %d waiting, posted in %d thread(s)", schedule_id, len(result.statuses), result.waiting, len(result.posted))
    if _nothing_left_to_remind(schedule_id, result):
        note = message.render_done_note(schedule_id, config.scope, config.lookback_hours, result.count(Bucket.INACCESSIBLE))
        _end_reminder(bot, schedule_id, config, "completed", note)
