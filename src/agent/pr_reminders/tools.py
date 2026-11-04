"""PR reminder tool handlers: check_pr_approvals (one-off) and schedule_pr_reminder (recurring)."""
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

from slack_sdk.errors import SlackApiError

from agent.pr_reminders.limits import ReminderRequestError, parse_mentions, validate_reminder_cron
from agent.pr_reminders.message import active_hours_text
from agent.pr_reminders.service import CHANNEL_READ_HELP, ChannelReadError, CheckResult, check_and_remind, collect_links, save_progress
from agent.pr_reminders.status import Bucket
from common.configs import AgentConfigs, ReminderConfig
from connectors import slack
from connectors.db.bots import BotConfig
from connectors.db.schedules import add_pr_reminder, count_active_pr_reminders, remove_schedule

log = logging.getLogger("agent.pr_reminders.tools")

_CHANNEL_ID_RE = re.compile(r"[CG][A-Z0-9]{6,}")

# Each linked thread costs one chat.getPermalink call; past this the summary just counts the rest.
_MAX_PERMALINKS = 5


def _parse_scope(args: dict) -> str:
    scope = args.get("scope")
    if scope not in AgentConfigs.PR_REMINDER_SCOPES:
        raise ReminderRequestError("scope must be 'thread' or 'channel'.")
    return scope


def _parse_mentions(args: dict) -> list[str]:
    mentions = parse_mentions(args.get("mentions") or [])
    if not mentions:
        raise ReminderRequestError(
            "Who should I remind? Ask the user to mention the people to remind (for example @alice @bob); I ping them in each PR thread."
        )
    return mentions


def _parse_lookback(args: dict) -> int:
    value = args.get("lookback_hours") or AgentConfigs.PR_REMINDER_DEFAULT_LOOKBACK_HOURS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReminderRequestError("lookback_hours must be a positive whole number of hours.")
    return min(value, AgentConfigs.PR_REMINDER_MAX_LOOKBACK_HOURS)


def _thread_links(bot: BotConfig, posted: list[tuple[str, str]]) -> list[str]:
    links = []
    for channel, thread_ts in posted:
        try:
            links.append(f"<{slack.get_permalink(channel, thread_ts, token=bot.bot_token)}|thread>")
        except SlackApiError:
            log.warning("Could not get a permalink for %s/%s", channel, thread_ts)
    return links


def _summarize(bot: BotConfig, result: CheckResult, scan_truncated: bool) -> str:
    waiting = result.waiting
    on_author = result.count(Bucket.WAITING_ON_AUTHOR)
    inaccessible = result.unreadable
    done = sum(result.count(bucket) for bucket in (Bucket.APPROVED, Bucket.DONE, Bucket.DRAFT))
    parts = [f"Checked {len(result.statuses)} PR(s): {waiting} waiting for review, {on_author} waiting on the author, {done} approved, closed or draft."]

    if inaccessible:
        parts.append(f"{inaccessible} PR(s) could not be read from GitHub (repo not visible to my credentials).")
    if result.truncated:
        parts.append(f"{result.truncated} older PR(s) were not checked (limit {AgentConfigs.PR_REMINDER_MAX_PRS_CHECKED}).")
    if scan_truncated:
        parts.append(f"The channel has more messages in that window than I scan (limit {AgentConfigs.PR_REMINDER_MAX_MESSAGES_SCANNED}), so older links may be missing.")
    if result.posted:
        links = ", ".join(_thread_links(bot, result.posted[:_MAX_PERMALINKS]))
        more = len(result.posted) - _MAX_PERMALINKS
        parts.append(f"Posted reminders in {len(result.posted)} thread(s){': ' + links if links else '.'}{f' and {more} more.' if more > 0 else ''}")
    if result.deferred_threads:
        parts.append(f"{result.deferred_threads} more thread(s) were not reminded this time (limit {AgentConfigs.PR_REMINDER_MAX_THREADS_POSTED} per run).")
    if result.failed_posts:
        parts.append(f"Could not post in {result.failed_posts} thread(s).")
    if not waiting and not on_author:
        incomplete = scan_truncated or result.truncated or inaccessible
        parts.append("I can't say nothing is waiting, because the check was incomplete." if incomplete else "Nothing is waiting for review :white_check_mark:")
    return " ".join(parts)


def _check_pr_approvals(args: dict, channel: str, thread_ts: str, bot: BotConfig) -> str:
    scope = _parse_scope(args)
    mentions = _parse_mentions(args)
    typed_urls = [url for url in args.get("pr_urls") or [] if isinstance(url, str)]
    scan = collect_links(
        bot,
        scope=scope,
        channel=channel,
        thread_ts=thread_ts,
        lookback_hours=_parse_lookback(args),
        typed_urls=typed_urls,
    )
    if not scan.links:
        where = "in this thread." if scope == "thread" else "in this channel's recent top-level messages."
        more = " The window has more messages than I scan, so older ones may be missing." if scan.truncated else ""
        return f"I didn't find any GitHub pull request links {where}{more}"
    return _summarize(bot, check_and_remind(bot, scan.links, mentions=mentions, include_author_only_threads=True), scan.truncated)


class _ScheduleRequest(NamedTuple):
    scope: str
    cron: str
    channel: str
    lookback_hours: int | None
    mentions: list[str]


def _parse_schedule_request(args: dict, channel: str) -> _ScheduleRequest:
    """Validate a schedule_pr_reminder request; `channel` is the one it was made in, the default channel to watch."""
    scope = _parse_scope(args)
    mentions = _parse_mentions(args)
    cron = str(args.get("cron") or "").strip()
    validate_reminder_cron(cron)

    target_channel = args.get("channel") or channel
    if target_channel != channel and not _CHANNEL_ID_RE.fullmatch(target_channel):
        raise ReminderRequestError(f"'{target_channel}' is not a Slack channel ID.")
    if scope == "thread" and target_channel != channel:
        raise ReminderRequestError("A thread-scope reminder watches the thread it was requested in, so it can't target another channel.")
    return _ScheduleRequest(scope, cron, target_channel, _parse_lookback(args) if scope == "channel" else None, mentions)


def _schedule_pr_reminder(args: dict, user_id: str | None, channel: str, thread_ts: str, bot: BotConfig) -> str:
    request = _parse_schedule_request(args, channel)
    if count_active_pr_reminders(bot.bot_id, request.channel) >= AgentConfigs.PR_REMINDER_MAX_ACTIVE_PER_CHANNEL:
        raise ReminderRequestError(f"This channel already has {AgentConfigs.PR_REMINDER_MAX_ACTIVE_PER_CHANNEL} active PR reminders. Remove one first.")

    config = ReminderConfig(request.scope, channel, thread_ts, tuple(request.mentions), request.lookback_hours, user_id)
    scan = collect_links(bot, scope=request.scope, channel=request.channel, thread_ts=thread_ts, lookback_hours=request.lookback_hours)

    # The schedule is kept even when nothing is waiting yet, since a PR can be shared later. The row goes in before the
    # first check posts, so the schedule id in those messages is real, and comes out again if that check fails.
    schedule_id = str(uuid.uuid4())
    add_pr_reminder(schedule_id, request.cron, request.channel, config.label(), bot.bot_id, config.to_dict())
    try:
        result = check_and_remind(bot, scan.links, mentions=request.mentions, schedule_id=schedule_id)
    except Exception:
        remove_schedule(schedule_id, bot_id=bot.bot_id)
        raise
    save_progress(schedule_id, config, result, datetime.now(UTC))

    first = (
        f"First reminders posted in {len(result.posted)} thread(s)."
        if result.waiting
        else "Nothing is waiting for review right now, so nothing was posted; I'll remind when a PR is."
    )
    return (
        f"PR reminder scheduled — id: `{schedule_id}`. {first} It repeats on `{request.cron}` (UTC) but only sends reminders "
        f"{active_hours_text()}, stops by itself once it has reminded about PRs and none is waiting any more, and ends after "
        f"{AgentConfigs.PR_REMINDER_LIFETIME_DAYS} days. Ask me to remove schedule `{schedule_id}` to stop it earlier."
    )


def dispatch_pr_reminder_tool(name: str, args: dict, user_id: str | None, channel: str, thread_ts: str, bot: BotConfig) -> str:
    """Run check_pr_approvals or schedule_pr_reminder and return the text to hand back to the LLM.

    The reminders themselves are posted here, not by the LLM; the return value is a summary, a refusal or an error
    message for the agent to relay. `thread_ts` is the request's thread, also the one scanned for scope "thread".
    """
    try:
        if name == "check_pr_approvals":
            return _check_pr_approvals(args, channel, thread_ts, bot)
        if name == "schedule_pr_reminder":
            return _schedule_pr_reminder(args, user_id, channel, thread_ts, bot)
    except ReminderRequestError as exc:
        return str(exc)
    except ChannelReadError as exc:
        return CHANNEL_READ_HELP.get(exc.code, f"I can't read that channel (Slack error: {exc.code}).")
    except Exception as exc:
        log.exception("PR reminder tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown PR reminder tool: {name}"
