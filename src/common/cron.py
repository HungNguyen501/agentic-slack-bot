"""Cron expression checks shared by the recurring jobs that admins schedule. Pure, no I/O."""
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

from croniter import croniter

_DAY_HORIZON = 800


class CronError(ValueError):
    """A cron expression is malformed or fires too often; the message is fit to show the user."""


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


def validate_cron(cron: str, min_interval_minutes: int, subject: str, now: datetime | None = None) -> None:
    """Reject cron expressions that are malformed or can fire more often than the minimum interval.

    Args:
        subject: What the cron schedules, as the start of the "too often" message (e.g. "A PR reminder").

    Raises:
        CronError: with a message fit to show the admin.
    """
    if len(cron.split()) != 5:
        raise CronError("The cron expression must have exactly 5 fields, e.g. '*/30 * * * *'.")
    base = now or datetime.now(UTC)
    try:
        croniter(cron, base).get_next(datetime)  # rejects malformed fields and dates that never exist
        shortest = _shortest_gap_minutes(cron, base.date())
    except (ValueError, KeyError) as exc:
        raise CronError(f"'{cron}' is not a valid cron expression.") from exc
    if shortest < min_interval_minutes:
        raise CronError(f"{subject} can run at most every {min_interval_minutes} minutes.")
