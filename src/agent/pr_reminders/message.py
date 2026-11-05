"""Render the Slack text PR reminders are posted with. Pure, no I/O.

The wording itself (Vietnamese, cheerful and comedic) lives in `common.configs.ReminderMessages`; this module only picks and
fills in the templates.

The only live mentions these messages can contain are the user IDs the caller passes (already validated as user
mentions); everything that comes from GitHub is escaped first so a PR title can't smuggle in a mention or a link.
"""
from datetime import datetime, timedelta

from agent.pr_reminders.status import PrStatus
from common.configs import AgentConfigs, ReminderMessages


def escape(text: str) -> str:
    """Escape the three characters Slack treats as control characters in mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_age(created_at: datetime | None, now: datetime) -> str:
    if created_at is None:
        return ReminderMessages.AGE_UNKNOWN
    minutes = max(0, int((now - created_at).total_seconds() // 60))
    if minutes >= 24 * 60:
        return ReminderMessages.AGE_DAYS.format(n=minutes // (24 * 60))
    if minutes >= 60:
        return ReminderMessages.AGE_HOURS.format(n=minutes // 60)
    return ReminderMessages.AGE_MINUTES.format(n=minutes)


def _hours_range() -> str:
    return f"{AgentConfigs.PR_REMINDER_ACTIVE_START_HOUR:02d}:00–{AgentConfigs.PR_REMINDER_ACTIVE_END_HOUR:02d}:00"


def active_hours_text() -> str:
    """The window recurring reminders post in, for the agent (not posted to Slack): "07:00–22:00 Vietnam time (UTC+7)"."""
    return ReminderMessages.ACTIVE_HOURS_FOR_AGENT.format(hours=_hours_range(), offset=AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS)


def _header(who: str, count: int, now: datetime) -> str:
    template = ReminderMessages.HEADERS[int(now.timestamp() // ReminderMessages.HEADER_ROTATION_SECONDS) % len(ReminderMessages.HEADERS)]
    subject = ReminderMessages.SUBJECT_ONE if count == 1 else ReminderMessages.SUBJECT_MANY.format(count=count)
    return template.format(who=who, subject=subject, Subject=subject[0].upper() + subject[1:])


def _age_flair(created_at: datetime | None, now: datetime) -> str:
    """A little quip for how long a PR has waited: fresh ones sparkle, long-forgotten ones gather dust."""
    if created_at is None:
        return ""
    age = now - created_at
    if age >= timedelta(days=ReminderMessages.FLAIR_ANCIENT_AFTER_DAYS):
        return ReminderMessages.FLAIR_ANCIENT
    if age >= timedelta(days=ReminderMessages.FLAIR_STALE_AFTER_DAYS):
        return ReminderMessages.FLAIR_STALE
    if age < timedelta(hours=ReminderMessages.FLAIR_FRESH_UNDER_HOURS):
        return ReminderMessages.FLAIR_FRESH
    return ""


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
        lines.append(_header(" ".join(f"<@{user_id}>" for user_id in mentions), len(waiting), now))
        lines.extend(
            _line(s, ReminderMessages.WAITING_LINE.format(age=format_age(s.created_at, now), flair=_age_flair(s.created_at, now))) for s in waiting
        )
    if on_author:
        lines.append(ReminderMessages.AUTHOR_COURT_WITH_WAITING if waiting else ReminderMessages.AUTHOR_COURT_ALONE)
        lines.extend(_line(s, ReminderMessages.AUTHOR_LINE) for s in on_author)
    if schedule_id:
        lines.append(
            ReminderMessages.SCHEDULE_FOOTER.format(
                schedule_id=schedule_id, hours=_hours_range(), offset=AgentConfigs.PR_REMINDER_UTC_OFFSET_HOURS
            )
        )
    return "\n".join(lines)


def render_expired_note(schedule_id: str) -> str:
    return ReminderMessages.EXPIRED.format(schedule_id=schedule_id)


def render_unreadable_note(schedule_id: str, error_code: str) -> str:
    return ReminderMessages.UNREADABLE.format(schedule_id=schedule_id, error_code=error_code)
