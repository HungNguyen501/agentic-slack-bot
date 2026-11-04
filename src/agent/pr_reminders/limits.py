"""Limits on PR reminders: how often one may run and how long it lives. Pure, no I/O."""
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

from croniter import croniter

from common.configs import AgentConfigs

_REMINDER_TZ = timezone(timedelta(hours=AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS))

# A user mention as Slack writes it (<@U123> or <@U123|name>) or a bare user ID. Channel, here and user-group
# mentions don't match, so a request can't be used to broadcast.
_USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>|([UW][A-Z0-9]+)")


class ReminderRequestError(ValueError):
    """A request to check or schedule PR reminders can't be honoured; the message is fit to show the user."""


def validate_reminder_cron(cron: str, now: datetime | None = None) -> None:
    """Reject cron expressions that are malformed or fire more often than the minimum interval.

    Raises:
        ReminderRequestError: with a message fit to show the admin.
    """
    if len(cron.split()) != 5:
        raise ReminderRequestError("The cron expression must have exactly 5 fields, e.g. '*/30 * * * *'.")
    base = now or datetime.now(UTC)
    try:
        iterator = croniter(cron, base)
        # Several firings, not two: an irregular pattern like "0,2 * * * *" has a short gap hiding in it.
        firings = [iterator.get_next(datetime) for _ in range(12)]
    except (ValueError, KeyError) as exc:
        raise ReminderRequestError(f"'{cron}' is not a valid cron expression.") from exc
    shortest_gap = min((later - earlier).total_seconds() for earlier, later in pairwise(firings))
    if shortest_gap < AgentConfigs.PR_REMINDER_MIN_INTERVAL_MINUTES * 60:
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
