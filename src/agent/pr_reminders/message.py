"""Render the Slack text PR reminders are posted with. Pure, no I/O.

The default wording (Vietnamese, cheerful and comedic) is `DEFAULT_VOICE` below. A character can replace any of it with its
own `pr_reminder` voice (see `PrReminderVoice`). The parts no character changes (age units, the waiting line, the quip
thresholds, the header rotation) are in `common.configs.ReminderMessages`. This module picks and fills in the templates.

The only live mentions these messages can contain are the user IDs the caller passes (already validated as user
mentions); everything that comes from GitHub is escaped first so a PR title can't smuggle in a mention or a link.
"""
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta

from agent.pr_reminders.configs import PrRemindersConfigs
from characters.core.configs import CharactersConfigs
from agent.pr_reminders.status import PrStatus
from characters.core.models import Character
from characters.core.validation import TemplateField, VoiceSpec
from common.configs import ReminderMessages


@dataclass(frozen=True)
class PrReminderVoice:
    """The wording a reminder is rendered with: the fields of a character's `pr_reminder` voice block."""

    headers: tuple[str, ...]
    subject_one: str
    subject_many: str
    flair_fresh: str
    flair_stale: str
    flair_ancient: str
    author_court_with_waiting: str
    author_court_alone: str
    author_line: str
    schedule_footer: str
    expired: str
    unreadable: str

    @classmethod
    def from_character(cls, character: Character | None) -> "PrReminderVoice":
        """The character's voice, with every field it doesn't set taken from the default; the default for no character."""
        block = character.voices.get(CharactersConfigs.FEATURE_PR_REMINDER, {}) if character else {}
        overrides = {f.name: tuple(block[f.name]) if f.name == "headers" else block[f.name] for f in fields(cls) if f.name in block}
        return replace(DEFAULT_VOICE, **overrides)


# The wording used when no character speaks, and for every field a character leaves out. Vietnamese, cheerful and comedic.
DEFAULT_VOICE = PrReminderVoice(
    # The header above a thread's PRs. {who} is the mentions, {subject} the PR phrase, {Subject} the same with a capital
    # letter for a template that starts a sentence with it.
    headers=(
        "{who} :eyes: Ủa alo, {subject} đang chờ review nè bà con ơi! :pepe-pray:",
        "{who} :bell: Dạ thưa quý vị, {subject} chờ review lâu quá trời quá đất luôn rồi nha!",
        "{who} :wave: Cốc cốc cốc! Có ai ở nhà không? {Subject} đang đứng đợi review nè!",
        "{who} :mag: Trời ơi tin được không? {subject} vẫn chưa ai review luôn á!",
    ),
    subject_one="cái PR này",
    subject_many="{count} cái PR này",
    # A quip after the age: fresh PRs sparkle, forgotten ones gather dust.
    flair_fresh=" :sparkles: mới tinh",
    flair_stale=" :cricket: dế gáy rồi nha",
    flair_ancient=" :spider_web: nhện giăng tơ luôn rồi",
    # PRs with changes requested: listed without a ping, under a heading that depends on whether PRs also wait for review.
    author_court_with_waiting="Bóng đang ở sân tác giả (cần sửa lại) :tennis:",
    author_court_alone=":tennis: PR này bị yêu cầu sửa lại, bóng đang ở sân tác giả nha",
    author_line="bị yêu cầu sửa lại",
    # The last line of a recurring reminder: how to stop it.
    schedule_footer='Lịch nhắc `{schedule_id}` — nhắn "xoá lịch nhắc {schedule_id}" để tui hết làm phiền nha.',
    # Notes posted in the thread the reminder was requested in.
    expired=":hourglass: Lịch nhắc `{schedule_id}` đã hết hạn như cả nhà đặt, tui xin phép nghỉ ngơi nha. Cần nhắc tiếp thì kêu tui lập lịch mới!",
    unreadable=(
        ":warning: Ủa, tui không thấy channel của lịch nhắc `{schedule_id}` nữa (lỗi Slack: `{error_code}`), nên tui treo giày nghỉ nhắc luôn. "
        "Kiểm tra giúp tui coi tui còn ở trong channel và có quyền đọc lịch sử (history scopes) không nha."
    ),
)


# What a character's `pr_reminder` block may contain: the placeholders each template of the default voice is filled with.
VOICE_SPEC = VoiceSpec(
    fields={
        "headers": TemplateField(
            placeholders=frozenset({"who", "subject", "Subject"}), required=frozenset({"who"}), one_of=frozenset({"subject", "Subject"}), many=True
        ),
        "subject_one": TemplateField(),
        "subject_many": TemplateField(placeholders=frozenset({"count"}), required=frozenset({"count"})),
        "flair_fresh": TemplateField(),
        "flair_stale": TemplateField(),
        "flair_ancient": TemplateField(),
        "author_court_with_waiting": TemplateField(),
        "author_court_alone": TemplateField(),
        "author_line": TemplateField(),
        "schedule_footer": TemplateField(placeholders=PrRemindersConfigs.SCHEDULE_ID, required=PrRemindersConfigs.SCHEDULE_ID),
        "expired": TemplateField(placeholders=PrRemindersConfigs.SCHEDULE_ID, required=PrRemindersConfigs.SCHEDULE_ID),
        "unreadable": TemplateField(placeholders=frozenset({"schedule_id", "error_code"}), required=frozenset({"schedule_id", "error_code"})),
    },
    mandatory=frozenset({"headers"}),
)


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


def _header(who: str, count: int, now: datetime, voice: PrReminderVoice) -> str:
    template = voice.headers[int(now.timestamp() // ReminderMessages.HEADER_ROTATION_SECONDS) % len(voice.headers)]
    subject = voice.subject_one if count == 1 else voice.subject_many.format(count=count)
    return template.format(who=who, subject=subject, Subject=subject[0].upper() + subject[1:])


def _age_flair(created_at: datetime | None, now: datetime, voice: PrReminderVoice) -> str:
    """A little quip for how long a PR has waited: fresh ones sparkle, long-forgotten ones gather dust."""
    if created_at is None:
        return ""
    age = now - created_at
    if age >= timedelta(days=ReminderMessages.FLAIR_ANCIENT_AFTER_DAYS):
        return voice.flair_ancient
    if age >= timedelta(days=ReminderMessages.FLAIR_STALE_AFTER_DAYS):
        return voice.flair_stale
    if age < timedelta(hours=ReminderMessages.FLAIR_FRESH_UNDER_HOURS):
        return voice.flair_fresh
    return ""


def _line(status: PrStatus, detail: str) -> str:
    # "|" would end the link label early inside Slack's <url|label> syntax.
    label = escape(f"{status.link.ref.slug} {status.title}").replace("|", "¦")
    return f"• <{status.link.ref.url}|{label}> — {detail}"


def render_thread_message(
    waiting: list[PrStatus],
    on_author: list[PrStatus],
    *,
    mentions: list[str],
    now: datetime,
    schedule_id: str | None,
    voice: PrReminderVoice = DEFAULT_VOICE,
) -> str | None:
    """Render the reminder for one thread, or None when it has no PR to talk about.

    The people in `mentions` are pinged only when a PR is in `waiting`; `on_author` PRs are listed without a ping.
    `schedule_id` is set for a recurring reminder, so the message tells people how to stop it. `voice` is the wording to use.
    """
    if not waiting and not on_author:
        return None

    lines = []
    if waiting:
        lines.append(_header(" ".join(f"<@{user_id}>" for user_id in mentions), len(waiting), now, voice))
        lines.extend(
            _line(s, ReminderMessages.WAITING_LINE.format(age=format_age(s.created_at, now), flair=_age_flair(s.created_at, now, voice))) for s in waiting
        )
    if on_author:
        lines.append(voice.author_court_with_waiting if waiting else voice.author_court_alone)
        lines.extend(_line(s, voice.author_line) for s in on_author)
    if schedule_id:
        lines.append(voice.schedule_footer.format(schedule_id=schedule_id))
    return "\n".join(lines)


def render_expired_note(schedule_id: str, voice: PrReminderVoice = DEFAULT_VOICE) -> str:
    return voice.expired.format(schedule_id=schedule_id)


def render_unreadable_note(schedule_id: str, error_code: str, voice: PrReminderVoice = DEFAULT_VOICE) -> str:
    return voice.unreadable.format(schedule_id=schedule_id, error_code=error_code)
