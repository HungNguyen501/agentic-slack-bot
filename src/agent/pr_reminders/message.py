"""Render the Slack text PR reminders are posted with. Pure, no I/O.

The only live mentions these messages can contain are the user IDs the caller passes (already validated as user
mentions); everything that comes from GitHub is escaped first so a PR title can't smuggle in a mention or a link.
"""
from datetime import datetime

from agent.pr_reminders.status import PrStatus
from common.configs import AgentConfigs


def escape(text: str) -> str:
    """Escape the three characters Slack treats as control characters in mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_age(created_at: datetime | None, now: datetime) -> str:
    if created_at is None:
        return "?"
    minutes = max(0, int((now - created_at).total_seconds() // 60))
    if minutes >= 24 * 60:
        return f"{minutes // (24 * 60)}d"
    if minutes >= 60:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def active_hours_text() -> str:
    """The window recurring reminders post in, e.g. "07:00–22:00 Vietnam time (UTC+7)"."""
    return (
        f"{AgentConfigs.PR_REMINDER_ACTIVE_START_HOUR:02d}:00–{AgentConfigs.PR_REMINDER_ACTIVE_END_HOUR:02d}:00 "
        f"Vietnam time (UTC+{AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS})"
    )


def _line(status: PrStatus, detail: str) -> str:
    # "|" would end the link label early inside Slack's <url|label> syntax.
    label = escape(f"{status.link.ref.slug} {status.title}").replace("|", "¦")
    return f"• <{status.link.ref.url}|{label}> — {detail}"


def render_thread_message(waiting: list[PrStatus], on_author: list[PrStatus], *, mentions: list[str], now: datetime, schedule_id: str | None) -> str | None:
    """Render the reminder for one thread, or None when it has no PR to talk about.

    The people in `mentions` are pinged only when a PR is in `waiting`; `on_author` PRs are listed without a ping.
    `schedule_id` is set for a recurring reminder, so the message tells people how to stop it and when it runs.
    """
    if not waiting and not on_author:
        return None

    lines = []
    if waiting:
        lines.append(f"{' '.join(f'<@{user_id}>' for user_id in mentions)} :eyes: Waiting for review")
        lines.extend(_line(s, f"open {format_age(s.created_at, now)}") for s in waiting)
    if on_author:
        lines.append("Changes requested, waiting on the author:" if waiting else ":speech_balloon: Changes requested, waiting on the author")
        lines.extend(_line(s, "changes requested") for s in on_author)
    if schedule_id:
        lines.append(f"Schedule `{schedule_id}` — ask me to remove it to stop. Reminders are only sent {active_hours_text()}.")
    return "\n".join(lines)


def render_done_note(schedule_id: str, scope: str, lookback_hours: int | None) -> str:
    where = "in this thread" if scope == "thread" else f"in the last {lookback_hours}h"
    return f":white_check_mark: No PRs are waiting for review {where} — stopping schedule `{schedule_id}`."


def render_expired_note(schedule_id: str, lifetime_days: int) -> str:
    return f":hourglass: Schedule `{schedule_id}` ran for {lifetime_days} days, so I'm stopping it. Ask me to set up a new reminder if you still need one."


def render_unreadable_note(schedule_id: str, error_code: str) -> str:
    return (
        f":warning: I can't read the channel for schedule `{schedule_id}` (Slack error: `{error_code}`), so I'm stopping it. "
        "Check that I'm a member of the channel and have the history scopes."
    )
