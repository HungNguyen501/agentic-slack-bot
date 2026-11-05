"""PR reminder tool handlers: check_pr_approvals (one-off) and schedule_pr_reminder (recurring)."""
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

from slack_sdk.errors import SlackApiError

from agent.pr_reminders.limits import ReminderRequestError, is_within_active_hours, parse_mentions, validate_reminder_cron
from agent.pr_reminders.message import active_hours_text
from agent.pr_reminders.service import CHANNEL_READ_HELP, ChannelReadError, CheckResult, check_and_remind, collect_links, save_progress
from agent.pr_reminders.status import Bucket
from common.configs import AgentConfigs, ReminderConfig
from connectors import slack
from connectors.db.bots import BotConfig
from connectors.db.schedules import Schedule, add_pr_reminder, remove_schedule

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
    raw = args.get("mentions") or []
    # A model sometimes sends "<@U1> <@U2>" as one string instead of a list.
    mentions = parse_mentions([part for part in re.split(r"[\s,]+", raw) if part] if isinstance(raw, str) else raw)
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


def _parse_expires_in_hours(args: dict) -> int | None:
    value = args.get("expires_in_hours")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReminderRequestError("expires_in_hours must be a positive whole number of hours.")
    return value


def _thread_links(bot: BotConfig, posted: list[tuple[str, str]]) -> list[str]:
    links = []
    for channel, thread_ts in posted:
        try:
            links.append(f"<{slack.get_permalink(channel, thread_ts, token=bot.bot_token)}|thread>")
        except SlackApiError:
            log.warning("Could not get a permalink for %s/%s", channel, thread_ts)
    return links


def _incomplete_notes(result: CheckResult, scan_truncated: bool) -> list[str]:
    """Sentences for each way a check can fall short of seeing every PR; empty when it was complete."""
    notes = []
    if result.unreadable:
        notes.append(f"{result.unreadable} PR(s) could not be read from GitHub (repo not visible to my credentials).")
    if result.truncated:
        notes.append(f"{result.truncated} older PR(s) were not checked (limit {AgentConfigs.PR_REMINDER_MAX_PRS_CHECKED}).")
    if scan_truncated:
        notes.append(f"The channel has more messages in that window than I scan (limit {AgentConfigs.PR_REMINDER_MAX_MESSAGES_SCANNED}), so older links may be missing.")
    return notes


def _summarize(bot: BotConfig, result: CheckResult, scan_truncated: bool) -> str:
    waiting = result.waiting
    on_author = result.count(Bucket.WAITING_ON_AUTHOR)
    done = sum(result.count(bucket) for bucket in (Bucket.APPROVED, Bucket.DONE, Bucket.DRAFT))
    parts = [f"Checked {len(result.statuses)} PR(s): {waiting} waiting for review, {on_author} waiting on the author, {done} approved, closed or draft."]

    notes = _incomplete_notes(result, scan_truncated)
    parts += notes
    if result.posted:
        links = ", ".join(_thread_links(bot, result.posted[:_MAX_PERMALINKS]))
        more = len(result.posted) - _MAX_PERMALINKS
        parts.append(f"Posted reminders in {len(result.posted)} thread(s){': ' + links if links else '.'}{f' and {more} more.' if more > 0 else ''}")
    if result.deferred_threads:
        parts.append(f"{result.deferred_threads} more thread(s) were not reminded this time (limit {AgentConfigs.PR_REMINDER_MAX_THREADS_POSTED} per run).")
    if result.failed_posts:
        parts.append(f"Could not post in {result.failed_posts} thread(s).")
    if not waiting and not on_author:
        parts.append("I can't say nothing is waiting, because the check was incomplete." if notes else "Nothing is waiting for review :white_check_mark:")
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
    expires_in_hours: int | None


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
    return _ScheduleRequest(scope, cron, target_channel, _parse_lookback(args) if scope == "channel" else None, mentions, _parse_expires_in_hours(args))


def _first_check(bot: BotConfig, request: _ScheduleRequest, config: ReminderConfig, schedule_id: str, thread_ts: str) -> str:
    """Run the check that follows scheduling, post any first reminders, and describe what happened.

    Outside the active hours nothing is read or posted: the first tick inside the window does it, so scheduling at
    night doesn't ping anyone.

    Raises only before anything is posted (Slack can't be read, GitHub can't be queried): `check_and_remind` handles
    its own post failures. A failure to save progress afterwards is reported in the text instead, because by then
    reminders may be out and the schedule they name must stay.
    """
    now = datetime.now(UTC)
    if not is_within_active_hours(now):
        return "It's outside the hours reminders are sent, so the first check waits for the first tick inside them."

    scan = collect_links(bot, scope=request.scope, channel=request.channel, thread_ts=thread_ts, lookback_hours=request.lookback_hours)
    result = check_and_remind(bot, scan.links, mentions=request.mentions, schedule_id=schedule_id)
    try:
        save_progress(schedule_id, config, result, now)
    except Exception:
        log.exception("Could not save the progress of PR reminder %s after its first check", schedule_id)
        return (
            f"First reminders posted in {len(result.posted)} thread(s), but I couldn't save their progress, "
            "so the next run may remind those threads again."
        )
    if result.waiting:
        return f"First reminders posted in {len(result.posted)} thread(s)."
    notes = _incomplete_notes(result, scan.truncated)
    if notes:
        return f"I couldn't confirm that nothing is waiting: {' '.join(notes)} I'll keep checking."
    return "Nothing is waiting for review right now, so nothing was posted; I'll remind when a PR is."


def _already_scheduled(existing: Schedule, request: _ScheduleRequest) -> str:
    """Reply to a request for a reminder that already exists: nothing is created, posted or checked, so repeating a
    request can't double the pings."""
    reply = (
        f"That PR reminder already exists (same channel, scope and people) — id: `{existing.id}`, repeating on `{existing.cron}` (UTC). "
        "I didn't create another."
    )
    if existing.cron != request.cron:
        reply += f" You asked for `{request.cron}`; to change it, ask me to remove schedule `{existing.id}` and set it up again."
    return reply


def _schedule_pr_reminder(args: dict, user_id: str | None, channel: str, thread_ts: str, bot: BotConfig) -> str:
    request = _parse_schedule_request(args, channel)
    expires_at = datetime.now(UTC).timestamp() + request.expires_in_hours * 3600 if request.expires_in_hours else None
    config = ReminderConfig(request.scope, channel, thread_ts, tuple(request.mentions), request.lookback_hours, user_id, expires_at=expires_at)

    # The schedule is kept even when nothing is waiting yet, since a PR can be shared later. The row goes in before the
    # first check posts, so the schedule id in those messages is real, and comes out again if that check fails.
    schedule_id = str(uuid.uuid4())
    quota = AgentConfigs.PR_REMINDER_MAX_ACTIVE_PER_CHANNEL
    added = add_pr_reminder(schedule_id, request.cron, request.channel, config.label(), bot.bot_id, config.to_dict(), max_active=quota)
    if added is None:
        raise ReminderRequestError(f"This channel already has {quota} active PR reminders. Remove one first.")
    if not added.created:
        return _already_scheduled(added.schedule, request)
    try:
        first = _first_check(bot, request, config, schedule_id, thread_ts)
    except Exception:
        remove_schedule(schedule_id, bot_id=bot.bot_id)
        raise

    ends = (
        f"ends by itself after {request.expires_in_hours} hours. Ask me to remove schedule `{schedule_id}` to stop it earlier."
        if request.expires_in_hours
        else f"keeps running, even when nothing is waiting, until it is removed. Ask me to remove schedule `{schedule_id}` to stop it."
    )
    return f"PR reminder scheduled — id: `{schedule_id}`. {first} It repeats on `{request.cron}` (UTC) but only sends reminders {active_hours_text()}, and {ends}"


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
