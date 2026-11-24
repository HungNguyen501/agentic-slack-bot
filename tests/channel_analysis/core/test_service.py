import pytest

from channel_analysis.core import limits
from channel_analysis.core.errors import ChannelAccessError, ChannelAnalysisError
from channel_analysis.core.models import FileMeta, FileRef, HistoryBatch
from channel_analysis.core.service import ChannelAnalysisService
from tests.support.channel_analysis import BOT, CHANNEL, NOW, WINDOW_START, make_info, make_message, resume_at, ts_ago

ADMIN = "UADMIN"


def read(service, **kwargs):
    return service.read(BOT, kwargs.pop("channel", CHANNEL), kwargs.pop("requester", ADMIN), **kwargs)


# --- read: the window, the filter, the pages


def test_read_returns_the_messages_of_the_window_newest_first(service, history):
    history.messages = [make_message(1, "a"), make_message(5, "b"), make_message(24 * 31, "too old")]

    page = read(service)

    assert [m.text for m in page.messages] == ["a", "b"]
    assert page.next_before is None
    assert page.window_end == NOW
    assert page.window_start == NOW - 30 * 86400


def test_read_asks_the_history_with_the_window_start_and_the_replies_choice(service, history):
    read(service, lookback_days=7, include_replies=False)

    fetch = history.fetches[0]
    assert fetch["oldest"] == NOW - 7 * 86400
    assert fetch["latest"] is None
    assert (fetch["limit"], fetch["with_replies"]) == (limits.FETCH_BATCH, False)


def test_read_with_replies_uses_the_smaller_batch(service, history):
    read(service)

    assert history.fetches[0]["limit"] == limits.FETCH_BATCH_WITH_REPLIES


def test_end_days_ago_moves_the_window_back(service, history):
    history.messages = [make_message(24, "yesterday"), make_message(24 * 8, "last week"), make_message(24 * 20, "older")]

    page = read(service, lookback_days=7, end_days_ago=7)

    assert [m.text for m in page.messages] == ["last week"]
    assert page.window_end == NOW - 7 * 86400
    assert page.window_start == NOW - 14 * 86400


def test_a_before_cursor_resumes_below_it_and_never_above_the_window_end(service, history):
    history.messages = [make_message(1, "new"), make_message(30, "old")]

    below = read(service, before=ts_ago(2))
    clamped = read(service, end_days_ago=1, lookback_days=5, before=ts_ago(0.5))

    assert [m.text for m in below.messages] == ["old"]
    assert history.fetches[-1]["latest"] == f"{NOW - 86400:.6f}"
    assert [m.text for m in clamped.messages] == ["old"]


def test_contains_keeps_a_message_with_any_term_in_text_author_file_or_reply(service, history):
    history.messages = [
        make_message(1, "tenant Bridgewell_com ok"),
        make_message(2, "nothing", author="hawthorne bot"),
        make_message(3, "nothing", files=(FileRef("F1", "bridgewell_rows.csv", "csv", 10),)),
        make_message(4, "nothing", replies=(make_message(4, "re: hawthorne_com"),)),
        make_message(5, "other tenant"),
    ]

    page = read(service, contains=("BRIDGEWELL_COM", " hawthorne ", "bridgewell_rows"))

    assert [m.ts for m in page.messages] == [ts_ago(1), ts_ago(2), ts_ago(3), ts_ago(4)]
    assert page.terms == ("bridgewell_com", "hawthorne", "bridgewell_rows")
    assert page.scanned == 5


def test_a_result_carries_the_window_start_and_a_resumed_call_keeps_the_same_window(history, files, extractor, monkeypatch):
    clock = [NOW]
    service = ChannelAnalysisService(history, files, extractor, now=lambda: clock[0])
    monkeypatch.setattr(limits, "PAGE_CHAR_BUDGET", 600)
    history.messages = [make_message(n, "x" * 200) for n in range(1, 5)]

    first = service.read(BOT, CHANNEL, "UADMIN")
    clock[0] += 900
    second = service.read(BOT, CHANNEL, "UADMIN", before=first.next_before)

    assert first.next_before.endswith(f"@{WINDOW_START}")
    assert (second.window_start, second.window_end) == (first.window_start, first.window_end)
    assert history.fetches[-1]["oldest"] == first.window_start


@pytest.mark.parametrize(
    "window_start",
    [f"{NOW - 400 * 86400:.6f}", f"{NOW - 31 * 86400:.6f}", f"{NOW + 3 * 86400:.6f}"],
    ids=["a-year-back", "past-the-reach", "in-the-future"],
)
def test_a_resume_token_cannot_move_the_window_outside_what_a_read_may_reach(service, history, window_start):
    with pytest.raises(ChannelAnalysisError, match="doesn't belong to a window"):
        read(service, before=f"{ts_ago(1)}@{window_start}")

    assert history.fetches == []


def test_a_resume_token_from_a_window_that_started_a_few_minutes_ago_is_accepted(service, history):
    page = read(service, before=f"{ts_ago(1)}@{NOW - 30 * 86400 - 900:.6f}")

    assert page.window_start == NOW - 30 * 86400 - 900


def test_a_bare_timestamp_still_resumes_with_a_window_from_now(service, history):
    history.messages = [make_message(1, "new"), make_message(30, "old")]

    page = read(service, before=ts_ago(2))

    assert [m.text for m in page.messages] == ["old"]
    assert page.window_start == NOW - 30 * 86400


def test_a_page_stops_at_the_budget_and_resumes_without_repeating(service, history, monkeypatch):
    monkeypatch.setattr(limits, "PAGE_CHAR_BUDGET", 1200)
    history.messages = [make_message(n, f"m{n}" + "x" * 300) for n in range(1, 8)]
    seen, before, pages = [], None, 0

    while True:
        page = read(service, before=before)
        seen.extend(m.ts for m in page.messages)
        pages += 1
        before = page.next_before
        if before is None:
            break

    assert seen == [m.ts for m in history.messages]
    assert pages > 2


def test_one_message_bigger_than_the_budget_is_still_returned(service, history, monkeypatch):
    monkeypatch.setattr(limits, "PAGE_CHAR_BUDGET", 10)
    history.messages = [make_message(1, "a" * 50), make_message(2, "b" * 50)]

    page = read(service)

    assert [m.ts for m in page.messages] == [ts_ago(1)]
    assert page.next_before == resume_at(1)


def test_the_scan_cap_returns_a_resume_cursor_even_when_nothing_matched(service, history, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SCAN_PER_CALL_WITH_REPLIES", 3)
    monkeypatch.setattr(limits, "FETCH_BATCH_WITH_REPLIES", 2)
    history.messages = [make_message(n, "noise") for n in range(1, 8)] + [make_message(9, "needle")]

    first = read(service, contains=("needle",))

    assert first.messages == ()
    assert first.scanned == 4
    assert first.next_before == resume_at(4)
    last = read(service, contains=("needle",), before=resume_at(6))
    assert [m.text for m in last.messages] == ["needle"]
    assert last.next_before is None


def test_a_channel_of_nothing_but_noise_stops_at_the_scan_cap_instead_of_fetching_on(service, history, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SCAN_PER_CALL_WITH_REPLIES", 50)
    monkeypatch.setattr(limits, "FETCH_BATCH_WITH_REPLIES", 10)
    history.script = [HistoryBatch([], ts_ago(n + 1), exhausted=False, fetched=10) for n in range(20)]

    page = read(service)

    assert len(history.fetches) == 5
    assert page.messages == ()
    assert page.next_before == resume_at(5)


def test_a_batch_of_only_noise_moves_the_cursor_on(service, history):
    history.script = [HistoryBatch([], ts_ago(3), exhausted=False, fetched=1), HistoryBatch([make_message(5, "real")], ts_ago(5), exhausted=True, fetched=1)]

    page = read(service)

    assert [m.text for m in page.messages] == ["real"]
    assert history.fetches[1]["latest"] == ts_ago(3)


def test_a_batch_with_nothing_at_all_ends_the_window(service, history):
    history.script = [HistoryBatch([], None, exhausted=False, fetched=0)]

    page = read(service)

    assert page.messages == ()
    assert page.next_before is None


def test_a_long_text_is_cut_and_marked(service, history):
    history.messages = [make_message(1, "x" * (limits.MESSAGE_CHAR_LIMIT + 500))]

    message = read(service).messages[0]

    assert message.text.endswith("…[cut]")
    assert len(message.text) < limits.MESSAGE_CHAR_LIMIT + 20


def test_a_term_past_the_cut_still_matches(service, history):
    history.messages = [make_message(1, "x" * (limits.MESSAGE_CHAR_LIMIT + 10) + " needle")]

    assert len(read(service, contains=("needle",)).messages) == 1


def test_replies_past_the_thread_limit_are_counted_not_shown(service, history, monkeypatch):
    monkeypatch.setattr(limits, "REPLIES_CHAR_LIMIT", 700)
    replies = tuple(make_message(1, "r" * 100, ts=f"{NOW - 3000 + n:.6f}") for n in range(6))
    history.messages = [make_message(1, "root", reply_count=6, replies=replies)]

    message = read(service).messages[0]

    assert len(message.replies) == 2
    assert message.omitted_replies == 4


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"lookback_days": 0}, "at least 1"),
        ({"end_days_ago": -1}, "at least 1"),
        ({"lookback_days": 31}, "30 days back"),
        ({"lookback_days": 20, "end_days_ago": 11}, "30 days back"),
        ({"contains": tuple("abcdef")}, "at most 5"),
        ({"before": "yesterday"}, "previous result"),
        ({"before": "1763900000.123456@soon"}, "previous result"),
    ],
    ids=["zero-days", "negative-end", "over-reach", "sum-over-reach", "too-many-terms", "bad-cursor", "bad-window-start"],
)
def test_read_refuses_bad_arguments(service, history, kwargs, message):
    with pytest.raises(ChannelAnalysisError, match=message):
        read(service, **kwargs)

    assert history.fetches == []


def test_blank_terms_are_ignored(service, history):
    history.messages = [make_message(1, "a")]

    assert len(read(service, contains=("", "  ")).messages) == 1


# --- access, enforced on every read


def test_read_resolves_a_channel_name(service, history):
    history.messages = [make_message(1, "a")]

    assert len(read(service, channel="#Mail-Reports").messages) == 1


def test_read_refuses_a_channel_it_cannot_find(service):
    with pytest.raises(ChannelAnalysisError, match="can't find that channel"):
        read(service, channel="#nope")


def test_read_refuses_a_channel_the_bot_is_not_in_without_reading_anything(service, history):
    history.infos[CHANNEL] = make_info(is_member=False)

    with pytest.raises(ChannelAnalysisError, match="not a member"):
        read(service)

    assert history.fetches == []


def test_read_refuses_a_private_channel_the_requester_is_not_in(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members = {"UOTHER": True}

    with pytest.raises(ChannelAnalysisError, match="you are not a member"):
        read(service)

    assert history.fetches == []


def test_read_allows_a_private_channel_the_requester_is_in(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members = {ADMIN: True}
    history.messages = [make_message(1, "a")]

    assert len(read(service).messages) == 1


def test_a_public_channel_needs_no_membership_check(service, history):
    read(service)

    assert history.member_checks == []


def test_read_refuses_a_private_channel_whose_membership_cannot_be_confirmed(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members = {ADMIN: None}

    with pytest.raises(ChannelAnalysisError, match="could not confirm"):
        read(service)


def test_read_passes_on_a_slack_refusal_with_its_fix(service, history):
    history.fetch_error = ChannelAccessError("missing_scope", detail="groups:history")

    with pytest.raises(ChannelAccessError, match="groups:history"):
        read(service)


# --- check_access


def check(service, channel=CHANNEL, requester=ADMIN):
    return service.check_access(BOT, channel, requester)


def test_check_access_is_ok_for_a_readable_public_channel(service, history):
    history.messages = [make_message(1)]

    access = check(service)

    assert access.ok
    assert access.problems == ()
    assert (access.channel_id, access.channel_name, access.empty_window) == (CHANNEL, "mail-reports", False)


def test_check_access_flags_a_channel_without_messages(service):
    assert check(service).empty_window is True


def test_check_access_reports_a_name_it_cannot_find(service):
    access = check(service, "#nope")

    assert not access.ok
    assert [p.code for p in access.problems] == ["channel_not_found"]
    assert access.channel_id is None


def test_check_access_reports_every_problem_at_once(service, history):
    history.infos[CHANNEL] = make_info(is_archived=True, is_member=False)

    access = check(service)

    assert [p.code for p in access.problems] == ["is_archived", "not_in_channel"]
    assert not access.ok
    assert history.fetches == []


def test_check_access_refuses_a_direct_message(service):
    with pytest.raises(ChannelAnalysisError, match="direct message"):
        check(service, "D0123456789")


def test_check_access_reports_a_private_channel_the_requester_is_not_in(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)

    access = check(service)

    assert [p.code for p in access.problems] == ["private_not_member"]
    assert not access.ok


@pytest.mark.parametrize("members", [{}, {ADMIN: None}], ids=["not-a-member", "unconfirmed"])
def test_a_requester_who_may_not_see_the_channel_learns_nothing_about_its_messages(service, history, members):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members = members
    history.messages = [make_message(1)]

    access = check(service)

    assert history.fetches == []
    assert access.empty_window is False
    assert not access.ok


def test_a_scope_problem_found_while_checking_the_requester_also_stops_the_probe(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members_error = ChannelAccessError("missing_scope", detail="groups:read")
    history.messages = [make_message(1)]

    access = check(service)

    assert [p.code for p in access.problems] == ["missing_scope"]
    assert history.fetches == []
    assert access.empty_window is False


def test_check_access_reports_a_membership_it_could_not_confirm(service, history):
    history.infos[CHANNEL] = make_info(is_private=True)
    history.members = {ADMIN: None}

    assert [p.code for p in check(service).problems] == ["membership_unverified"]


def test_a_missing_info_scope_is_reported_and_nothing_is_read(service, history):
    history.info_error = ChannelAccessError("missing_scope", detail="channels:read")

    access = check(service)

    assert [p.code for p in access.problems] == ["missing_scope"]
    assert not access.ok
    assert history.fetches == []


def test_a_history_scope_problem_found_by_the_probe_blocks(service, history):
    history.fetch_error = ChannelAccessError("missing_scope", detail="channels:history")

    access = check(service)

    assert [p.code for p in access.problems] == ["missing_scope"]
    assert not access.ok


def test_no_file_permission_is_only_a_warning(service, history):
    history.files_readable = False
    history.messages = [make_message(1)]

    access = check(service)

    assert access.ok
    assert [(p.code, p.blocking) for p in access.problems] == [("files_scope", False)]


def test_a_slack_error_while_probing_files_is_a_warning_that_keeps_the_other_results(service, history):
    history.files_error = ChannelAccessError("ratelimited")
    history.messages = [make_message(1)]

    access = check(service)

    assert access.ok
    assert [(p.code, p.blocking) for p in access.problems] == [("ratelimited", False)]
    assert access.channel_name == "mail-reports"


# --- read_file


@pytest.fixture
def csv_file(files):
    files.metas["F1"] = FileMeta("rows.csv", "csv", 100, frozenset({CHANNEL}))
    files.texts["F1"] = "id,n\n" + "".join(f"{n},x\n" for n in range(10))


def test_read_file_returns_the_first_page_and_where_the_next_starts(service, csv_file, monkeypatch):
    monkeypatch.setattr(limits, "FILE_PAGE_CHARS", 20)

    page = service.read_file(BOT, CHANNEL, ADMIN, "F1")

    assert page.text.startswith("id,n\n0,x\n")
    assert page.next_offset is not None and page.next_offset < page.total_chars
    assert (page.name, page.filetype, page.offset) == ("rows.csv", "csv", 0)


def test_a_later_csv_page_repeats_the_header(service, csv_file, monkeypatch):
    monkeypatch.setattr(limits, "FILE_PAGE_CHARS", 20)
    first = service.read_file(BOT, CHANNEL, ADMIN, "F1")

    second = service.read_file(BOT, CHANNEL, ADMIN, "F1", offset=first.next_offset)

    assert second.text.startswith("id,n\n")
    assert second.offset == first.next_offset


def test_a_text_file_has_no_header_repeated(service, files, monkeypatch):
    monkeypatch.setattr(limits, "FILE_PAGE_CHARS", 20)
    files.metas["F2"] = FileMeta("notes.txt", "text", 100, frozenset({CHANNEL}))
    files.texts["F2"] = "".join(f"line {n}\n" for n in range(10))

    second = service.read_file(BOT, CHANNEL, ADMIN, "F2", offset=20)

    assert not second.text.startswith("line 0")


def test_read_file_refuses_a_file_shared_in_another_channel(service, files):
    files.metas["F3"] = FileMeta("secret.csv", "csv", 10, frozenset({"C9999999999"}))

    with pytest.raises(ChannelAnalysisError, match="isn't shared in this channel"):
        service.read_file(BOT, CHANNEL, ADMIN, "F3")


def test_read_file_refuses_a_file_over_the_size_limit_before_downloading(service, files):
    files.metas["F4"] = FileMeta("big.csv", "csv", limits.FILE_MAX_BYTES + 1, frozenset({CHANNEL}))

    with pytest.raises(ChannelAnalysisError, match="up to 5 MB"):
        service.read_file(BOT, CHANNEL, ADMIN, "F4")


def test_read_file_applies_the_channel_checks_too(service, history, csv_file):
    history.infos[CHANNEL] = make_info(is_private=True)

    with pytest.raises(ChannelAnalysisError, match="you are not a member"):
        service.read_file(BOT, CHANNEL, ADMIN, "F1")


def test_read_file_refuses_a_negative_offset(service, csv_file):
    with pytest.raises(ChannelAnalysisError, match="negative"):
        service.read_file(BOT, CHANNEL, ADMIN, "F1", offset=-1)
