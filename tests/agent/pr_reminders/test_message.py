from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent.pr_reminders.message import (
    DEFAULT_VOICE,
    VOICE_SPEC,
    PrReminderVoice,
    escape,
    format_age,
    render_expired_note,
    render_thread_message,
    render_unreadable_note,
)
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.status import Bucket, PrStatus
from characters.core.models import Character
from characters.core.validation import character_problems
from common.configs import ReminderMessages

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
MENTIONS = ["U1", "U2"]


def status(number=1, title="Fix retry logic", age=timedelta(days=2), bucket=Bucket.WAITING):
    return PrStatus(PrLink(PrRef("org", "repo", number), "C1", "1.0", "1.0"), bucket, title, NOW - age)


@pytest.mark.parametrize(
    "age, expected",
    [
        (timedelta(minutes=5), "5 phút"),
        (timedelta(minutes=59), "59 phút"),
        (timedelta(hours=6, minutes=30), "6 giờ"),
        (timedelta(days=2, hours=5), "2 ngày"),
        (timedelta(minutes=-3), "0 phút"),
    ],
    ids=["minutes", "just-under-an-hour", "hours", "days", "clock-skew"],
)
def test_format_age(age, expected):
    assert format_age(NOW - age, NOW) == expected


def test_format_age_without_a_creation_time():
    assert format_age(None, NOW) == "?"


def test_escape_neutralises_slack_control_characters():
    assert escape("<!channel> & <@U1>") == "&lt;!channel&gt; &amp; &lt;@U1&gt;"


def test_render_thread_message_mentions_the_people_and_lists_waiting_prs():
    text = render_thread_message([status(1), status(2, "Add view", timedelta(hours=6))], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert text == (
        "<@U1> <@U2> :eyes: Ủa alo, 2 cái PR này đang chờ review nè bà con ơi! :pepe-pray:\n"
        "• <https://github.com/org/repo/pull/1|org/repo#1 Fix retry logic> — chờ 2 ngày\n"
        "• <https://github.com/org/repo/pull/2|org/repo#2 Add view> — chờ 6 giờ"
    )


def test_render_thread_message_speaks_of_one_pr_in_the_singular():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert text.splitlines()[0] == "<@U1> <@U2> :eyes: Ủa alo, cái PR này đang chờ review nè bà con ơi! :pepe-pray:"


@pytest.mark.parametrize(
    "slot, header",
    [
        (0, ":eyes: Ủa alo, 3 cái PR này đang chờ review nè bà con ơi! :pepe-pray:"),
        (1, ":bell: Dạ thưa quý vị, 3 cái PR này chờ review lâu quá trời quá đất luôn rồi nha!"),
        (2, ":wave: Cốc cốc cốc! Có ai ở nhà không? 3 cái PR này đang đứng đợi review nè!"),
        (3, ":mag: Trời ơi tin được không? 3 cái PR này vẫn chưa ai review luôn á!"),
    ],
    ids=["eyes", "bell", "wave", "mag"],
)
def test_render_thread_message_rotates_its_header_every_half_hour(slot, header):
    prs = [status(1), status(2), status(3)]

    text = render_thread_message(prs, [], mentions=MENTIONS, now=NOW + timedelta(minutes=30 * slot), schedule_id=None)

    assert text.splitlines()[0] == f"<@U1> <@U2> {header}"


def test_a_header_that_starts_a_sentence_starts_with_a_capital_letter():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=60), schedule_id=None)

    assert "Có ai ở nhà không? Cái PR này đang đứng đợi review nè!" in text


def test_render_thread_message_keeps_the_same_header_within_a_half_hour_and_changes_it_after():
    first = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None).splitlines()[0]
    same_slot = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=29), schedule_id=None).splitlines()[0]
    next_slot = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=30), schedule_id=None).splitlines()[0]

    assert first == same_slot
    assert first != next_slot


@pytest.mark.parametrize(
    "age, flair",
    [
        (timedelta(minutes=5), " :sparkles: mới tinh"),
        (timedelta(minutes=59), " :sparkles: mới tinh"),
        (timedelta(hours=1), ""),
        (timedelta(days=2, hours=23), ""),
        (timedelta(days=3), " :cricket: dế gáy rồi nha"),
        (timedelta(days=6, hours=23), " :cricket: dế gáy rồi nha"),
        (timedelta(days=7), " :spider_web: nhện giăng tơ luôn rồi"),
        (timedelta(days=30), " :spider_web: nhện giăng tơ luôn rồi"),
    ],
    ids=["fresh", "just-under-an-hour", "an-hour", "almost-three-days", "three-days", "almost-a-week", "a-week", "a-month"],
)
def test_render_thread_message_adds_a_little_quip_for_how_long_a_pr_has_waited(age, flair):
    text = render_thread_message([status(1, age=age)], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert text.splitlines()[1].endswith(f"chờ {format_age(NOW - age, NOW)}{flair}")


def test_render_thread_message_never_lets_a_title_ping_anyone_else():
    text = render_thread_message([status(title="<!channel> hi <@U9> | x")], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "<!channel>" not in text
    assert "<@U9>" not in text
    assert text.splitlines()[0].startswith("<@U1> <@U2> ")
    assert "&lt;!channel&gt; hi &lt;@U9&gt; ¦ x" in text


def test_render_thread_message_lists_author_prs_below_the_ping():
    text = render_thread_message([status(1)], [status(2, bucket=Bucket.WAITING_ON_AUTHOR)], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "Bóng đang ở sân tác giả (cần sửa lại) :tennis:" in text
    assert text.index("<@U1>") < text.index("sân tác giả")
    assert text.splitlines()[-1].endswith("— bị yêu cầu sửa lại")


def test_render_thread_message_without_waiting_prs_does_not_ping():
    text = render_thread_message([], [status(2, bucket=Bucket.WAITING_ON_AUTHOR)], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "<@" not in text
    assert text.splitlines()[0] == ":tennis: PR này bị yêu cầu sửa lại, bóng đang ở sân tác giả nha"


def test_render_thread_message_for_a_recurring_reminder_shows_only_how_to_stop_it():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a")

    assert text.splitlines()[-1] == 'Lịch nhắc `3f2a` — nhắn "xoá lịch nhắc 3f2a" để tui hết làm phiền nha.'


def test_render_thread_message_for_a_one_off_check_does_not_mention_the_schedule_or_hours():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "Lịch nhắc" not in text
    assert "giờ Việt Nam" not in text


def test_render_thread_message_is_none_when_there_is_nothing_to_say():
    assert render_thread_message([], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a") is None


def test_render_expired_note_says_the_time_limit_was_reached_and_how_to_get_another():
    note = render_expired_note("3f2a")

    assert "`3f2a`" in note
    assert "đã hết hạn" in note
    assert "kêu tui lập lịch mới" in note


def test_render_unreadable_note_gives_the_slack_error_and_what_to_check():
    note = render_unreadable_note("3f2a", "not_in_channel")

    assert "`3f2a`" in note
    assert "`not_in_channel`" in note
    assert "còn ở trong channel" in note
    assert "history scopes" in note


@pytest.mark.parametrize("header", DEFAULT_VOICE.headers, ids=lambda header: header[:30])
def test_every_default_header_mentions_the_people_and_names_the_prs(header):
    assert "{who}" in header
    assert "{subject}" in header or "{Subject}" in header


def test_there_are_several_default_headers_to_rotate_through():
    assert len(DEFAULT_VOICE.headers) >= 2


def test_every_default_template_can_be_filled_with_the_values_this_module_passes():
    DEFAULT_VOICE.subject_many.format(count=3)
    DEFAULT_VOICE.schedule_footer.format(schedule_id="3f2a")
    DEFAULT_VOICE.expired.format(schedule_id="3f2a")
    DEFAULT_VOICE.unreadable.format(schedule_id="3f2a", error_code="not_in_channel")
    for header in DEFAULT_VOICE.headers:
        header.format(who="<@U1>", subject="cái PR này", Subject="Cái PR này")


def test_the_wording_comes_from_the_voice_it_is_rendered_with():
    pr = status(1, age=timedelta(days=4))
    changes = status(2, bucket=Bucket.WAITING_ON_AUTHOR)
    voice = replace(
        DEFAULT_VOICE,
        headers=("{who} HEADER {subject}",),
        subject_one="ONE",
        flair_stale=" STALE",
        author_court_with_waiting="COURT",
        author_line="FIX",
        schedule_footer="FOOT {schedule_id}",
    )
    with patch.object(ReminderMessages, "WAITING_LINE", "WAITED {age}{flair}"), patch.object(ReminderMessages, "AGE_DAYS", "{n}D"):
        text = render_thread_message([pr], [changes], mentions=MENTIONS, now=NOW, schedule_id="3f2a", voice=voice)

    assert text.splitlines()[0] == "<@U1> <@U2> HEADER ONE"
    assert text.splitlines()[1].endswith("— WAITED 4D STALE")
    assert text.splitlines()[2] == "COURT"
    assert text.splitlines()[3].endswith("— FIX")
    assert text.splitlines()[4] == "FOOT 3f2a"


def test_the_plural_subject_and_the_other_quips_come_from_the_voice():
    voice = replace(DEFAULT_VOICE, headers=("{who} {Subject}",), subject_many="{count} MANY", flair_fresh=" FRESH", flair_ancient=" ANCIENT")

    two_fresh = [status(1, age=timedelta(minutes=5)), status(2, age=timedelta(minutes=5))]
    fresh = render_thread_message(two_fresh, [], mentions=MENTIONS, now=NOW, schedule_id=None, voice=voice)
    ancient = render_thread_message([status(1, age=timedelta(days=30))], [], mentions=MENTIONS, now=NOW, schedule_id=None, voice=voice)

    assert fresh.splitlines()[0] == "<@U1> <@U2> 2 MANY"
    assert fresh.splitlines()[1].endswith(" FRESH")
    assert ancient.splitlines()[1].endswith(" ANCIENT")


def test_the_alone_court_line_comes_from_the_voice():
    voice = replace(DEFAULT_VOICE, author_court_alone="ALONE")

    text = render_thread_message([], [status(2, bucket=Bucket.WAITING_ON_AUTHOR)], mentions=MENTIONS, now=NOW, schedule_id=None, voice=voice)

    assert text.splitlines()[0] == "ALONE"


def test_the_closing_notes_come_from_the_voice():
    voice = replace(DEFAULT_VOICE, expired="EXPIRED {schedule_id}", unreadable="UNREADABLE {schedule_id} {error_code}")

    assert render_expired_note("s1", voice) == "EXPIRED s1"
    assert render_unreadable_note("s1", "not_in_channel", voice) == "UNREADABLE s1 not_in_channel"


def test_a_title_stays_escaped_in_a_custom_voice():
    voice = replace(DEFAULT_VOICE, headers=("{who} custom {subject}",))

    text = render_thread_message([status(title="<!channel> hi")], [], mentions=MENTIONS, now=NOW, schedule_id=None, voice=voice)

    assert "<!channel>" not in text
    assert text.splitlines()[0] == "<@U1> <@U2> custom cái PR này"


# --- PrReminderVoice.from_character ------------------------------------------------------------------


def _character(block=None, **overrides):
    voices = {} if block is None else {"pr_reminder": block}
    return Character(name="c", display_name="C", voices=voices, **overrides)


def test_the_default_voice_is_used_without_a_character():
    assert PrReminderVoice.from_character(None) == DEFAULT_VOICE


def test_the_default_voice_is_used_when_the_character_has_no_pr_reminder_block():
    assert PrReminderVoice.from_character(_character()) == DEFAULT_VOICE


def test_a_character_replaces_only_the_fields_it_sets():
    voice = PrReminderVoice.from_character(_character({"headers": ["{who} {subject} A", "{who} {subject} B"], "author_line": "FIX"}))

    assert voice.headers == ("{who} {subject} A", "{who} {subject} B")
    assert voice.author_line == "FIX"
    assert voice.expired == DEFAULT_VOICE.expired
    assert voice.subject_one == DEFAULT_VOICE.subject_one


def test_the_voice_spec_accepts_the_default_wording():
    block = {f.name: list(getattr(DEFAULT_VOICE, f.name)) if f.name == "headers" else getattr(DEFAULT_VOICE, f.name) for f in fields(PrReminderVoice)}

    assert character_problems(_character(block, bindings=("feature:pr_reminder",)), {"pr_reminder": VOICE_SPEC}) == []


def test_the_voice_spec_covers_every_field_of_the_voice():
    assert set(VOICE_SPEC.fields) == {f.name for f in fields(PrReminderVoice)}


def test_the_header_rotation_period_comes_from_the_config_class():
    with patch.object(ReminderMessages, "HEADER_ROTATION_SECONDS", 3600):
        now_header = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None).splitlines()[0]
        half_hour_later = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=30), schedule_id=None).splitlines()[0]
        hour_later = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=60), schedule_id=None).splitlines()[0]

    assert now_header == half_hour_later  # still inside the same one-hour slot
    assert now_header != hour_later
