"""Limits on PR reminders: how often one may run and how long it lives. Pure, no I/O."""
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from common.configs import AgentConfigs
from common.cron import CronError, validate_cron

_REMINDER_TZ = timezone(timedelta(hours=AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS))

# A user mention as Slack writes it (<@U123ABCDE> or <@U123ABCDE|name>) or a bare user ID. Channel, here and user-group
# mentions don't match, so a request can't be used to broadcast. Real IDs are at least 9 characters; requiring that keeps
# a word like UNKNOWN or WHO, which an LLM might pass, from becoming a mention that pings nobody.
_USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]{8,})(?:\|[^>]*)?>|([UW][A-Z0-9]{8,})")


class ReminderRequestError(ValueError):
    """A request to check or schedule PR reminders can't be honoured; the message is fit to show the user."""


def validate_reminder_cron(cron: str, now: datetime | None = None) -> None:
    """Reject cron expressions that are malformed or can fire more often than the minimum interval.

    Raises:
        ReminderRequestError: with a message fit to show the admin.
    """
    try:
        validate_cron(cron, AgentConfigs.PR_REMINDER_MIN_INTERVAL_MINUTES, "A PR reminder", now)
    except CronError as exc:
        raise ReminderRequestError(str(exc)) from exc


def is_reminder_expired(expires_at: float | None, now: datetime) -> bool:
    """Whether a reminder with a time limit (`ReminderConfig.expires_at`, unix time) has reached it; one without never expires."""
    return expires_at is not None and now.timestamp() >= expires_at


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
