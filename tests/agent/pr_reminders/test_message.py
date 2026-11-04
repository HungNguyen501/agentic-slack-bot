from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent.pr_reminders.message import (
    active_hours_text,
    escape,
    format_age,
    render_done_note,
    render_expired_note,
    render_thread_message,
    render_unreadable_note,
)
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.status import Bucket, PrStatus
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


def test_active_hours_text_stays_in_english_because_the_agent_reads_it():
    assert active_hours_text() == "07:00–22:00 Vietnam time (UTC+7)"


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


def test_render_thread_message_for_a_recurring_reminder_shows_only_the_schedule_id_and_the_hours():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a")

    assert text.splitlines()[-1] == (
        'Lịch nhắc `3f2a` — nhắn "xoá lịch nhắc 3f2a" để tui hết làm phiền nha. '
        "Tui chỉ nhắc từ 07:00–22:00 giờ Việt Nam (UTC+7) thôi, tối còn đi ngủ :zzz:"
    )


def test_render_thread_message_for_a_one_off_check_does_not_mention_the_schedule_or_hours():
    text = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None)

    assert "Lịch nhắc" not in text
    assert "giờ Việt Nam" not in text


def test_render_thread_message_is_none_when_there_is_nothing_to_say():
    assert render_thread_message([], [], mentions=MENTIONS, now=NOW, schedule_id="3f2a") is None


@pytest.mark.parametrize("scope, lookback, where", [("thread", None, "trong thread này"), ("channel", 72, "trong 72 giờ qua")])
def test_render_done_note_names_where_it_looked(scope, lookback, where):
    note = render_done_note("3f2a", scope, lookback)

    assert where in note
    assert "`3f2a`" in note
    assert note.startswith(":tada: Quá đã luôn! Không còn PR nào chờ review")


def test_render_expired_note_says_how_long_it_ran_and_how_to_get_another():
    note = render_expired_note("3f2a", 7)

    assert "`3f2a`" in note
    assert "đã làm phiền cả nhà 7 ngày rồi" in note
    assert "kêu tui lập lịch mới" in note


def test_render_unreadable_note_gives_the_slack_error_and_what_to_check():
    note = render_unreadable_note("3f2a", "not_in_channel")

    assert "`3f2a`" in note
    assert "`not_in_channel`" in note
    assert "còn ở trong channel" in note
    assert "history scopes" in note


def test_the_wording_comes_from_the_config_class_so_it_can_be_retuned_there():
    pr = status(1, age=timedelta(days=4))
    changes = status(2, bucket=Bucket.WAITING_ON_AUTHOR)
    with (
        patch.object(ReminderMessages, "HEADERS", ("{who} HEADER {subject}",)),
        patch.object(ReminderMessages, "SUBJECT_ONE", "ONE"),
        patch.object(ReminderMessages, "WAITING_LINE", "WAITED {age}{flair}"),
        patch.object(ReminderMessages, "AGE_DAYS", "{n}D"),
        patch.object(ReminderMessages, "FLAIR_STALE", " STALE"),
        patch.object(ReminderMessages, "AUTHOR_COURT_WITH_WAITING", "COURT"),
        patch.object(ReminderMessages, "AUTHOR_LINE", "FIX"),
        patch.object(ReminderMessages, "SCHEDULE_FOOTER", "FOOT {schedule_id} {hours} {offset}"),
    ):
        text = render_thread_message([pr], [changes], mentions=MENTIONS, now=NOW, schedule_id="3f2a")

    assert text.splitlines()[0] == "<@U1> <@U2> HEADER ONE"
    assert text.splitlines()[1].endswith("— WAITED 4D STALE")
    assert text.splitlines()[2] == "COURT"
    assert text.splitlines()[3].endswith("— FIX")
    assert text.splitlines()[4] == "FOOT 3f2a 07:00–22:00 7"


def test_the_closing_notes_also_come_from_the_config_class():
    with (
        patch.object(ReminderMessages, "DONE", "DONE {where} {schedule_id}"),
        patch.object(ReminderMessages, "DONE_WHERE_CHANNEL", "{hours}H"),
        patch.object(ReminderMessages, "EXPIRED", "EXPIRED {schedule_id} {days}"),
        patch.object(ReminderMessages, "UNREADABLE", "UNREADABLE {schedule_id} {error_code}"),
    ):
        assert render_done_note("s1", "channel", 48) == "DONE 48H s1"
        assert render_expired_note("s1", 7) == "EXPIRED s1 7"
        assert render_unreadable_note("s1", "not_in_channel") == "UNREADABLE s1 not_in_channel"


def test_the_header_rotation_period_comes_from_the_config_class():
    with patch.object(ReminderMessages, "HEADER_ROTATION_SECONDS", 3600):
        now_header = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW, schedule_id=None).splitlines()[0]
        half_hour_later = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=30), schedule_id=None).splitlines()[0]
        hour_later = render_thread_message([status(1)], [], mentions=MENTIONS, now=NOW + timedelta(minutes=60), schedule_id=None).splitlines()[0]

    assert now_header == half_hour_later  # still inside the same one-hour slot
    assert now_header != hour_later
