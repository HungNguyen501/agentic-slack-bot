"""Limits on PR reminders: how often one may run and how long it lives. Pure, no I/O."""
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise

from croniter import croniter

from common.configs import AgentConfigs

_REMINDER_TZ = timezone(timedelta(hours=AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS))

_DAY_HORIZON = 800

# A user mention as Slack writes it (<@U123ABCDE> or <@U123ABCDE|name>) or a bare user ID. Channel, here and user-group
# mentions don't match, so a request can't be used to broadcast. Real IDs are at least 9 characters; requiring that keeps
# a word like UNKNOWN or WHO, which an LLM might pass, from becoming a mention that pings nobody.
_USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]{8,})(?:\|[^>]*)?>|([UW][A-Z0-9]{8,})")


class ReminderRequestError(ValueError):
    """A request to check or schedule PR reminders can't be honoured; the message is fit to show the user."""


def _daily_firing_minutes(cron: str) -> list[int]:
    """The minutes of the day (0-1439) a cron fires at on a day it is eligible, sorted."""
    expanded, _ = croniter.expand(cron)
    minutes, hours = expanded[0], expanded[1]
    return sorted(h * 60 + m for h in (range(24) if "*" in hours else hours) for m in (range(60) if "*" in minutes else minutes))


def _shortest_gap_minutes(cron: str, today: date) -> float:
    """The shortest time between two firings, exactly: within a day, and across the days the expression is eligible on.

    Looking at the next few firings isn't enough, e.g. "0,40 0,2,23 * * *" fires at 23:40 and then at 00:00, 20 minutes
    later, but only after a day's worth of firings. Eligible days are looked up over two years, which covers every
    day-of-week / day-of-month / month pattern except leap days (a single firing every four years has no short gap).
    """
    times = _daily_firing_minutes(cron)
    shortest = min((later - earlier for earlier, later in pairwise(times)), default=float("inf"))

    day_filter = "0 0 " + " ".join(cron.split()[2:])
    eligible = [
        today + timedelta(days=offset)
        for offset in range(_DAY_HORIZON)
        if croniter.match(day_filter, datetime.combine(today + timedelta(days=offset), datetime.min.time()))
    ]
    for earlier, later in pairwise(eligible):
        shortest = min(shortest, (later - earlier).days * 24 * 60 - times[-1] + times[0])
    return shortest


def validate_reminder_cron(cron: str, now: datetime | None = None) -> None:
    """Reject cron expressions that are malformed or can fire more often than the minimum interval.

    Raises:
        ReminderRequestError: with a message fit to show the admin.
    """
    if len(cron.split()) != 5:
        raise ReminderRequestError("The cron expression must have exactly 5 fields, e.g. '*/30 * * * *'.")
    base = now or datetime.now(UTC)
    try:
        croniter(cron, base).get_next(datetime)  # rejects malformed fields and dates that never exist
        shortest = _shortest_gap_minutes(cron, base.date())
    except (ValueError, KeyError) as exc:
        raise ReminderRequestError(f"'{cron}' is not a valid cron expression.") from exc
    if shortest < AgentConfigs.PR_REMINDER_MIN_INTERVAL_MINUTES:
        raise ReminderRequestError(f"A PR reminder can run at most every {AgentConfigs.PR_REMINDER_MIN_INTERVAL_MINUTES} minutes.")


def is_reminder_expired(created_at: str | None, now: datetime) -> bool:
    """Whether a reminder created at `created_at` (as the schedules table returns it) has outlived its lifetime."""
    if not created_at:
        return False
    return now - datetime.fromisoformat(created_at) > timedelta(days=AgentConfigs.PR_REMINDER_LIFETIME_DAYS)


def is_within_active_hours(now: datetime) -> bool:
    """Whether a recurring reminder may post at `now`: from the start hour up to, not including, the end hour, Vietnam time.

    Raises:
        ValueError: `now` is naive; `astimezone` would silently read it as the host's local time.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return AgentConfigs.PR_REMINDER_ACTIVE_START_HOUR <= now.astimezone(_REMINDER_TZ).hour < AgentConfigs.PR_REMINDER_ACTIVE_END_HOUR


def parse_mentions(values: Iterable[object]) -> list[str]:
    """Turn the mentions an LLM passed into distinct Slack user IDs, in order.

    Raises:
        ReminderRequestError: a value isn't a user mention, or there are more than the limit.
    """
    user_ids: dict[str, None] = {}
    for value in values:
        match = _USER_MENTION_RE.fullmatch(value.strip()) if isinstance(value, str) else None
        if match is None:
            raise ReminderRequestError(f"I can only remind people by @mention, and '{value}' isn't one. Mention each person as @name.")
        user_ids[match.group(1) or match.group(2)] = None
    if len(user_ids) > AgentConfigs.PR_REMINDER_MAX_MENTIONS:
        raise ReminderRequestError(f"I can remind at most {AgentConfigs.PR_REMINDER_MAX_MENTIONS} people at once.")
    return list(user_ids)
