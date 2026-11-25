import logging
import re
import time

import pytest

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import ChannelAccessError, ChannelAnalysisError, ExtractionError, ExtractionShapeError, FileUnreadableError
from channel_analysis.core.models import ExtractionField, ExtractionSpec, FileRef
from tests.support.channel_analysis import BOT, CHANNEL, NOW, WINDOW_START, make_info, make_message, resume_at, ts_ago

ADMIN = "UADMIN"
SPEC = ExtractionSpec(
    "one record per table row of the report",
    (ExtractionField("table", "the table"), ExtractionField("status", "what happened", ("skipped", "failed", "loaded"))),
)


def analyze(service, **kwargs):
    return service.analyze(BOT, kwargs.pop("channel", CHANNEL), kwargs.pop("requester", ADMIN), kwargs.pop("spec", SPEC), **kwargs)


def raising(error: Exception):
    """A function for a fake's method that raises `error` when it is called."""

    def fail(*args, **kwargs):
        raise error

    return fail


def numbers(user: str) -> list[int]:
    return [int(n) for n in re.findall(r"^\[m(\d+)\] ", user, re.M)]


def one_record_per_item(table="agility_po_costs", status="skipped"):
    def respond(system, user):
        return {"records": [{"m": n, "table": table, "status": status} for n in numbers(user)]}

    return respond


def tables(*names):
    """Answer with one record per item, taking each item's table from the text of the message."""

    def respond(system, user):
        records = []
        for block in re.split(r"\n\n(?=\[m\d+\] )", user):
            number = int(re.match(r"\[m(\d+)\]", block).group(1))
            records.extend({"m": number, "table": name, "status": "skipped"} for name in names if name in block)
        return {"records": records}

    return respond


def small_chunks(monkeypatch, budget=300, chunks=2):
    monkeypatch.setattr(ChannelAnalysisConfigs, "CHUNK_CHAR_BUDGET", budget)
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_CHUNKS_PER_CALL", chunks)


# --- the happy path


def test_records_are_counted_by_the_group_by_fields(service, history, extractor):
    history.messages = [make_message(1, "po costs"), make_message(2, "po costs"), make_message(3, "dispatch")]
    extractor.respond = tables("po costs", "dispatch")

    result = analyze(service, group_by=("table",))

    counts = {g.key: (g.records, g.messages) for g in result.groups}
    assert counts == {("po costs",): (2, 2), ("dispatch",): (1, 1)}
    assert (result.messages_read, result.records, result.rejected, result.chunks) == (3, 3, 0, 1)
    assert result.fields == ("table", "status") and result.group_by == ("table",)
    assert result.next_before is None and result.failed_chunks == ()


def test_the_model_is_asked_with_the_prompt_for_the_spec_and_the_numbered_chunk(service, history, extractor):
    history.messages = [make_message(1, "first"), make_message(2, "second")]

    analyze(service)

    system, user = extractor.calls[0]
    assert "one record per table row of the report" in system
    assert user.count("[m1]") == 1 and user.count("[m2]") == 1
    assert "first" in user and "second" in user


def test_a_window_with_no_matching_message_asks_the_model_nothing(service, history, extractor):
    history.messages = [make_message(1, "other")]

    result = analyze(service, contains=("bridgewell",))

    assert extractor.calls == []
    assert (result.chunks, result.records, result.groups, result.messages_read, result.scanned) == (0, 0, (), 0, 1)


def test_the_result_carries_the_window_the_filter_and_the_scan(service, history, extractor):
    history.messages = [make_message(1, "bridgewell_com ok"), make_message(2, "hawthorne_com")]

    result = analyze(service, contains=("BRIDGEWELL_COM",), lookback_days=7)

    assert result.terms == ("bridgewell_com",)
    assert (result.messages_read, result.scanned) == (1, 2)
    assert result.window_end - result.window_start == 7 * 86400
    assert result.channel_id == CHANNEL


def test_the_same_record_twice_in_one_message_counts_once_but_not_across_messages(service, history, extractor):
    history.messages = [make_message(1, "a"), make_message(2, "b")]
    extractor.respond = lambda system, user: {"records": [{"m": n, "table": "t", "status": "failed"} for n in numbers(user) for _ in (1, 2)]}

    result = analyze(service, group_by=("table",))

    assert (result.records, result.groups[0].messages) == (2, 2)


def test_rejected_records_are_counted_and_the_valid_ones_kept(service, history, extractor):
    history.messages = [make_message(1, "a")]
    extractor.respond = lambda system, user: {
        "records": [{"m": 1, "table": "t", "status": "skipped"}, {"m": 9, "table": "t", "status": "skipped"}, {"m": 1, "status": "exploded"}]
    }

    result = analyze(service)

    assert (result.records, result.rejected) == (1, 2)


def test_the_sample_is_newest_first_and_capped(service, history, extractor, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "SAMPLE_RECORDS", 2)
    history.messages = [make_message(n, f"m{n}") for n in (1, 2, 3)]
    extractor.respond = one_record_per_item()

    result = analyze(service)

    assert [r.ts for r in result.sample] == [ts_ago(1), ts_ago(2)]
    assert result.records == 3


def test_groups_past_the_cap_are_summarized_not_listed(service, history, extractor, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_GROUPS_LISTED", 2)
    history.messages = [make_message(1, "t_alpha"), make_message(2, "t_beta"), make_message(3, "t_gamma"), make_message(4, "t_gamma")]
    extractor.respond = tables("t_alpha", "t_beta", "t_gamma")

    result = analyze(service, group_by=("table",))

    assert [g.key for g in result.groups] == [("t_gamma",), ("t_alpha",)]
    assert (result.groups_omitted, result.records_omitted) == (1, 1)


# --- chunks, the cap and resuming


def test_messages_are_packed_into_chunks_and_every_chunk_is_extracted(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=8)
    history.messages = [make_message(n, "x" * 150) for n in range(1, 5)]
    extractor.respond = one_record_per_item()

    result = analyze(service)

    assert (result.chunks, len(extractor.calls), result.records) == (4, 4, 4)


def test_a_window_needing_more_chunks_than_a_call_makes_comes_back_with_a_cursor_and_resuming_covers_every_message_once(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=2)
    history.messages = [make_message(n, "x" * 200) for n in range(1, 8)]
    extractor.respond = one_record_per_item()
    seen, before, calls = [], None, 0

    while True:
        result = analyze(service, before=before)
        seen.extend(r.ts for r in result.sample)
        calls += 1
        before = result.next_before
        if before is None:
            break

    assert sorted(seen) == sorted(m.ts for m in history.messages)
    assert len(seen) == len(set(seen)) and calls > 2


def test_the_cursor_carries_the_window_start_like_a_read(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=1)
    history.messages = [make_message(n, "x" * 200) for n in range(1, 4)]

    result = analyze(service)

    assert result.next_before == resume_at(1)
    assert result.next_before.endswith(f"@{WINDOW_START}")


def test_a_message_bigger_than_a_whole_call_is_still_taken_so_the_call_makes_progress(service, history, extractor, files, monkeypatch):
    small_chunks(monkeypatch, budget=100, chunks=1)
    monkeypatch.setattr(ChannelAnalysisConfigs, "ITEM_CHAR_LIMIT", 90)
    files.texts["F1"] = "line\n" * 100
    history.messages = [make_message(1, "report", files=(FileRef("F1", "log.txt", "text", 500),)), make_message(2, "older")]
    extractor.respond = one_record_per_item()

    result = analyze(service, include_files=True)

    assert result.messages_read == 1
    assert result.next_before == resume_at(1)


def test_a_scan_that_stops_at_its_cap_returns_a_cursor(service, history, extractor, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_SCAN_PER_CALL_ANALYSIS_WITH_REPLIES", 3)
    monkeypatch.setattr(ChannelAnalysisConfigs, "FETCH_BATCH_WITH_REPLIES", 2)
    history.messages = [make_message(n, "noise") for n in range(1, 9)]

    result = analyze(service, contains=("needle",))

    assert result.messages_read == 0 and result.chunks == 0
    assert result.next_before == resume_at(4)


def test_the_extraction_scan_cap_is_bigger_than_a_page_reads(service, history, extractor):
    history.messages = [make_message(n, "x") for n in range(1, 4)]

    analyze(service, include_replies=False)

    assert ChannelAnalysisConfigs.MAX_SCAN_PER_CALL_ANALYSIS > ChannelAnalysisConfigs.MAX_SCAN_PER_CALL
    assert history.fetches[0]["limit"] == ChannelAnalysisConfigs.FETCH_BATCH


# --- failures


def test_a_failed_chunk_is_named_by_its_time_range_and_left_out_of_the_counts(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=8)
    history.messages = [make_message(1, "ok one " + "x" * 200), make_message(2, "BROKEN " + "x" * 200), make_message(3, "ok two " + "x" * 200)]

    def respond(system, user):
        return ExtractionError("The extraction model failed (APITimeoutError).") if "BROKEN" in user else one_record_per_item()(system, user)

    extractor.respond = respond

    result = analyze(service)

    assert result.records == 2 and result.chunks == 3
    (failed,) = result.failed_chunks
    assert (failed.start_ts, failed.end_ts, failed.messages) == (ts_ago(2), ts_ago(2), 1)
    assert "APITimeoutError" in failed.reason
    assert result.unreliable is False


def test_more_than_half_the_chunks_failing_makes_the_result_unreliable(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=8)
    history.messages = [make_message(n, "x" * 200) for n in range(1, 4)]
    extractor.respond = lambda system, user: ExtractionError("down")

    result = analyze(service)

    assert (len(result.failed_chunks), result.unreliable, result.records) == (3, True, 0)


def test_a_failed_model_call_is_not_asked_again(service, history, extractor):
    history.messages = [make_message(1, "a")]
    extractor.respond = lambda system, user: ExtractionError("timeout")

    analyze(service)

    assert len(extractor.calls) == 1


def test_an_answer_of_the_wrong_shape_is_asked_for_once_more(service, history, extractor):
    history.messages = [make_message(1, "a")]
    answers = [ExtractionShapeError("The answer was not JSON."), {"records": [{"m": 1, "table": "t", "status": "failed"}]}]
    extractor.respond = lambda system, user: answers.pop(0)

    result = analyze(service)

    assert (len(extractor.calls), result.records, result.failed_chunks) == (2, 1, ())


@pytest.mark.parametrize("bad", [{"nope": 1}, ExtractionShapeError("not JSON")], ids=["no-records-list", "not-json"])
def test_a_second_wrong_answer_fails_the_chunk(service, history, extractor, bad):
    history.messages = [make_message(1, "a")]
    extractor.respond = lambda system, user: bad

    result = analyze(service)

    assert len(extractor.calls) == 2
    assert [f.messages for f in result.failed_chunks] == [1]


def test_results_do_not_depend_on_which_chunk_finishes_first(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=8)
    history.messages = [make_message(n, f"msg{n} " + "x" * 200) for n in range(1, 5)]

    def respond(system, user):
        if "msg1 " in user:
            time.sleep(0.05)
        return one_record_per_item()(system, user)

    extractor.respond = respond

    result = analyze(service)

    assert result.records == 4 and [r.ts for r in result.sample] == [ts_ago(n) for n in range(1, 5)]


def test_the_chunks_run_on_several_threads(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=8)
    history.messages = [make_message(n, "x" * 200) for n in range(1, 5)]
    started = time.monotonic()

    def respond(system, user):
        time.sleep(0.2)
        return {"records": []}

    extractor.respond = respond

    analyze(service)

    assert time.monotonic() - started < 0.7


def test_the_cost_and_time_of_a_call_are_logged(service, history, extractor, caplog):
    history.messages = [make_message(1, "a"), make_message(2, "b")]
    extractor.tokens = (1200, 300)

    with caplog.at_level(logging.INFO, logger="channel_analysis.service"):
        analyze(service)

    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("analyze "))
    assert "prompt_tokens=1200" in line and "completion_tokens=300" in line and "chunks=1" in line and "failed=0" in line and "chunk_seconds=[" in line


def logged(caplog) -> str:
    return next(r.getMessage() for r in caplog.records if r.getMessage().startswith("analyze "))


def test_a_wrong_shaped_answer_was_paid_for_so_its_tokens_are_counted_and_the_retry_is_too(service, history, extractor, caplog):
    history.messages = [make_message(1, "a")]
    answers = [ExtractionShapeError("The answer was not JSON.", 700, 40), {"records": [{"m": 1, "table": "t", "status": "failed"}]}]
    extractor.respond = lambda system, user: answers.pop(0)
    extractor.tokens = (500, 20)

    with caplog.at_level(logging.INFO, logger="channel_analysis.service"):
        analyze(service)

    line = logged(caplog)
    assert "prompt_tokens=1200" in line and "completion_tokens=60" in line


def test_a_chunk_that_failed_twice_on_shape_still_reports_what_both_calls_cost(service, history, extractor, caplog):
    history.messages = [make_message(1, "a")]
    extractor.respond = lambda system, user: ExtractionShapeError("The answer was not JSON.", 900, 100)

    with caplog.at_level(logging.INFO, logger="channel_analysis.service"):
        result = analyze(service)

    line = logged(caplog)
    assert len(result.failed_chunks) == 1
    assert "prompt_tokens=1800" in line and "completion_tokens=200" in line and "failed=1" in line


def test_an_answer_with_no_records_list_counts_the_call_once_not_twice(service, history, extractor, caplog):
    history.messages = [make_message(1, "a")]
    extractor.respond = lambda system, user: {"nope": 1}
    extractor.tokens = (100, 10)

    with caplog.at_level(logging.INFO, logger="channel_analysis.service"):
        analyze(service)

    assert "prompt_tokens=200" in logged(caplog) and "completion_tokens=20" in logged(caplog)


# --- files


def test_files_are_not_read_unless_asked(service, history, extractor, files):
    files.texts["F1"] = "never read"
    history.messages = [make_message(1, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]

    result = analyze(service)

    assert "never read" not in extractor.calls[0][1] and result.files_not_read == ()
    assert result.files_included is False


def test_a_file_becomes_more_items_of_its_message(service, history, extractor, files):
    files.texts["F1"] = "table,status\nagility_po_costs,skipped\n"
    history.messages = [make_message(1, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]
    extractor.respond = one_record_per_item()

    result = analyze(service, include_files=True)

    user = extractor.calls[0][1]
    assert "agility_po_costs,skipped" in user and "file rows.csv (part 1)" in user
    assert result.files_included is True
    # the message and its file both say the same row, and one message counts a row once
    assert result.messages_read == 1 and result.records == 1 and result.groups[0].messages == 1


@pytest.mark.parametrize(
    "ref, text_error, expected",
    [
        (FileRef("F1", "huge.csv", "csv", ChannelAnalysisConfigs.FILE_MAX_BYTES + 1), None, ("huge.csv", "it is larger than 5 MB")),
        (FileRef("F1", "scan.pdf", "pdf", 10), FileUnreadableError("That PDF has no text layer."), ("scan.pdf", "That PDF has no text layer.")),
        (FileRef("F1", "x.csv", "csv", 10), ChannelAnalysisError("I can't find that file."), ("x.csv", "I can't find that file.")),
    ],
    ids=["too-big", "unreadable", "missing"],
)
def test_a_file_that_cannot_be_read_is_listed_with_the_reason_and_the_message_is_still_analyzed(service, history, extractor, files, ref, text_error, expected):
    history.messages = [make_message(1, "report", files=(ref,))]
    files.text = raising(text_error or AssertionError("a file over the size limit is refused before it is downloaded"))

    result = analyze(service, include_files=True)

    assert result.files_not_read == (expected,)
    assert result.messages_read == 1


def test_when_slack_refuses_files_the_rest_are_not_tried(service, history, extractor, files):
    calls = []

    def text(file_id, token):
        calls.append(file_id)
        raise ChannelAccessError("files_scope")

    files.text = text
    history.messages = [
        make_message(1, "a", files=(FileRef("F1", "a.csv", "csv", 5), FileRef("F2", "b.csv", "csv", 5))),
        make_message(2, "b", files=(FileRef("F3", "c.csv", "csv", 5),)),
    ]

    result = analyze(service, include_files=True)

    assert calls == ["F1"]
    assert [name for name, _ in result.files_not_read] == ["a.csv", "b.csv", "c.csv"]
    assert all("files:read" in why for _, why in result.files_not_read)


def test_a_call_reads_at_most_the_allowed_number_of_files(service, history, extractor, files, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_FILES_PER_CALL", 2)
    for n in (1, 2, 3):
        files.texts[f"F{n}"] = f"file {n}"
    history.messages = [make_message(1, "a", files=tuple(FileRef(f"F{n}", f"f{n}.txt", "text", 5) for n in (1, 2, 3)))]

    result = analyze(service, include_files=True)

    assert [name for name, _ in result.files_not_read] == ["f3.txt"]
    assert "at most 2 files" in result.files_not_read[0][1]


def test_a_message_adds_only_so_many_pieces_and_says_a_file_was_cut(service, history, extractor, files, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "ITEM_CHAR_LIMIT", 40)
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_ITEMS_PER_MESSAGE", 3)
    files.texts["F1"] = "line of text\n" * 100
    history.messages = [make_message(1, "report", files=(FileRef("F1", "log.txt", "text", 500),))]

    analyze(service, include_files=True)

    user = "\n".join(call[1] for call in extractor.calls)
    assert user.count("(part ") == 2
    # (part 3) is not there: the message item itself is the first of the three
    assert "(part 3)" not in user


def test_a_message_that_does_not_fit_leaves_its_files_to_the_next_call_and_is_not_counted_twice(service, history, extractor, files, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=1)
    files.texts["F1"] = "table,status\nt,skipped\n"
    history.messages = [make_message(1, "x" * 250), make_message(2, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]

    first = analyze(service, include_files=True)
    second = analyze(service, include_files=True, before=first.next_before)

    assert first.files_not_read == () and second.files_not_read == ()
    assert second.messages_read == 1 and "rows.csv" in extractor.calls[-1][1]


def counting_downloads(files):
    downloads = []
    read = files.text

    def text(file_id, token):
        downloads.append(file_id)
        return read(file_id, token)

    files.text = text
    return downloads


def test_a_message_that_cannot_fit_is_not_enriched_with_its_files_at_all(service, history, extractor, files, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=1)
    files.texts["F1"] = "table,status\nt,skipped\n"
    downloads = counting_downloads(files)
    history.messages = [make_message(1, "x" * 250), make_message(2, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]

    first = analyze(service, include_files=True)

    assert first.next_before == resume_at(1) and first.messages_read == 1
    assert downloads == []


def test_the_files_of_a_message_that_was_deferred_after_reading_them_are_not_downloaded_again(service, history, extractor, files, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=1)
    files.texts["F1"] = "y" * 400
    downloads = counting_downloads(files)
    history.messages = [make_message(1, "x" * 100), make_message(2, "report", files=(FileRef("F1", "rows.txt", "text", 10),))]

    first = analyze(service, include_files=True)
    second = analyze(service, include_files=True, before=first.next_before)

    assert first.messages_read == 1 and first.next_before == resume_at(1)
    assert second.messages_read == 1 and second.files_not_read == ()
    assert "y" * 400 in extractor.calls[-1][1]
    assert downloads == ["F1"]


# --- arguments and access


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"group_by": ("nope",)}, "not one of the fields"),
        ({"spec": ExtractionSpec("", SPEC.fields)}, "how to fill one record"),
        ({"lookback_days": 31}, "30 days back"),
        ({"contains": tuple("abcdef")}, "at most 5"),
        ({"before": "yesterday"}, "previous result"),
    ],
)
def test_bad_arguments_are_refused_before_anything_is_read(service, history, extractor, kwargs, message):
    with pytest.raises(ChannelAnalysisError, match=message):
        analyze(service, **kwargs)

    assert history.fetches == [] and extractor.calls == []


def test_a_resume_token_cannot_move_the_window_outside_the_reach(service, history, extractor):
    with pytest.raises(ChannelAnalysisError, match="doesn't belong to a window"):
        analyze(service, before=f"{ts_ago(1)}@{1_700_000_000:.6f}")

    assert history.fetches == []


def test_the_channel_checks_apply_to_an_analysis_too(service, history, extractor):
    history.infos[CHANNEL] = make_info(is_private=True)

    with pytest.raises(ChannelAnalysisError, match="you are not a member"):
        analyze(service)

    assert history.fetches == [] and extractor.calls == []


# --- what the extractor sees is the whole message, not the page's display cut


def test_a_row_past_the_display_cut_reaches_the_extractor_and_a_page_read_still_cuts_it(service, history, extractor):
    body = "".join(f"row {n:04d} ok\n" for n in range(400)) + "row 9999 table_needle failed"
    assert len(body) > ChannelAnalysisConfigs.MESSAGE_CHAR_LIMIT
    history.messages = [make_message(1, body)]
    extractor.respond = one_record_per_item(table="table_needle")

    result = analyze(service, contains=("table_needle",))
    page = service.read(BOT, CHANNEL, ADMIN, contains=("table_needle",))

    sent = "\n".join(call[1] for call in extractor.calls)
    assert "row 9999 table_needle failed" in sent
    assert result.messages_read == 1
    assert "row 9999" not in page.messages[0].text and page.messages[0].text.endswith("…[cut]")


def test_replies_past_the_pages_display_budget_reach_the_extractor(service, history, extractor, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "REPLIES_CHAR_LIMIT", 300)
    replies = tuple(make_message(0.5, f"reply {n} " + "x" * 120, ts=f"{1799990000 + n}.000000") for n in range(10))
    history.messages = [make_message(1, "root", replies=replies)]

    analyze(service)
    page = service.read(BOT, CHANNEL, ADMIN)

    assert "reply 9 " in "\n".join(call[1] for call in extractor.calls)
    assert page.messages[0].omitted_replies > 0


def test_a_message_longer_than_one_message_may_add_is_counted_as_cut(service, history, extractor, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "ITEM_CHAR_LIMIT", 100)
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_ITEMS_PER_MESSAGE", 2)
    history.messages = [make_message(1, "line of text\n" * 60), make_message(2, "short")]

    result = analyze(service)

    assert (result.messages_read, result.messages_cut) == (2, 1)


# --- files attached to replies, and matching on their names


REPLY_FILE = FileRef("F8", "late_rows.csv", "csv", 20)


def thread_with_a_file_in_a_reply():
    reply = make_message(0.5, "here is the late report", author="<@U7>", files=(REPLY_FILE,))
    return make_message(2, "daily report", link="https://x/root", replies=(reply,), reply_count=1), reply


def test_a_file_attached_to_a_reply_is_read_and_counted_under_the_thread_root(service, history, extractor, files):
    root, reply = thread_with_a_file_in_a_reply()
    files.texts["F8"] = "table,status\nlate_table,failed\n"
    history.messages = [root]
    extractor.respond = one_record_per_item()

    result = analyze(service, include_files=True)

    user = extractor.calls[0][1]
    assert "late_table,failed" in user and "file late_rows.csv" in user and "<@U7>" in user
    assert result.files_not_read == ()
    assert result.sample[0].link == "https://x/root" and result.messages_read == 1


def test_a_reply_file_that_cannot_be_read_is_listed_not_silently_skipped(service, history, extractor, files):
    root, _ = thread_with_a_file_in_a_reply()
    history.messages = [root]
    files.text = raising(FileUnreadableError("That PDF has no text layer."))

    result = analyze(service, include_files=True)

    assert result.files_not_read == (("late_rows.csv", "That PDF has no text layer."),)


def test_files_of_the_root_and_of_its_replies_share_the_files_budget(service, history, extractor, files, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_FILES_PER_CALL", 1)
    files.texts.update({"F1": "root file", "F8": "reply file"})
    reply = make_message(0.5, "r", files=(REPLY_FILE,))
    history.messages = [make_message(2, "root", files=(FileRef("F1", "root.txt", "text", 5),), replies=(reply,))]

    result = analyze(service, include_files=True)

    assert [name for name, _ in result.files_not_read] == ["late_rows.csv"]
    assert "root file" in extractor.calls[0][1] and "reply file" not in extractor.calls[0][1]


def test_a_term_that_is_only_the_name_of_a_file_in_a_reply_matches_the_message(service, history, extractor):
    root, _ = thread_with_a_file_in_a_reply()
    history.messages = [root, make_message(3, "unrelated")]

    result = analyze(service, contains=("late_rows",))
    page = service.read(BOT, CHANNEL, ADMIN, contains=("late_rows",))

    assert result.messages_read == 1 and len(page.messages) == 1


def test_a_workbooks_later_pages_of_the_file_tool_start_with_their_sheet_and_columns(service, files, history, monkeypatch):
    from channel_analysis.core.models import FileMeta

    monkeypatch.setattr(ChannelAnalysisConfigs, "FILE_PAGE_CHARS", 200)
    files.metas["FX"] = FileMeta("counts.xlsx", "xlsx", 100, frozenset({CHANNEL}))
    files.texts["FX"] = "## Sheet: Counts\ntable,rows\n" + "".join(f"t{n},{n}\n" for n in range(60))
    first = service.read_file(BOT, CHANNEL, ADMIN, "FX")

    second = service.read_file(BOT, CHANNEL, ADMIN, "FX", offset=first.next_offset)

    assert first.text.startswith("## Sheet: Counts\ntable,rows\n")
    assert second.text.startswith("## Sheet: Counts\ntable,rows\n")


# --- the upper bound of the window, replies that were not read, and what a file attempt costs


def test_a_message_newer_than_the_moment_the_window_was_fixed_is_not_in_it(service, history, extractor):
    history.messages = [make_message(-1, "posted after the call started"), make_message(1, "inside")]
    extractor.respond = one_record_per_item()

    result = analyze(service)
    page = service.read(BOT, CHANNEL, ADMIN)

    assert result.messages_read == 1 and [m.text for m in page.messages] == ["inside"]
    assert history.fetches[0]["latest"] == f"{NOW:.6f}"


def test_the_replies_of_a_thread_are_asked_for_only_up_to_the_end_of_the_window(service, history, extractor):
    analyze(service, end_days_ago=7, lookback_days=7)

    assert history.fetches[0]["replies_until"] == NOW - 7 * 86400
    assert history.fetches[0]["latest"] == f"{NOW - 7 * 86400:.6f}"


def test_replies_that_were_not_read_are_part_of_the_coverage_of_an_analysis(service, history, extractor):
    history.messages = [make_message(1, "root", reply_count=40, omitted_replies=40), make_message(2, "other", reply_count=5, omitted_replies=2)]

    result = analyze(service)

    assert result.replies_omitted == 42


def test_replies_left_out_of_a_message_that_did_not_fit_are_counted_by_the_call_that_takes_it(service, history, extractor, monkeypatch):
    small_chunks(monkeypatch, budget=300, chunks=1)
    history.messages = [make_message(1, "x" * 250), make_message(2, "y" * 250, omitted_replies=7)]

    first = analyze(service)
    second = analyze(service, before=first.next_before)

    assert (first.replies_omitted, second.replies_omitted) == (0, 7)


def test_every_attempt_at_a_file_counts_against_the_files_a_call_may_try_even_when_it_fails(service, history, extractor, files, monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "MAX_FILES_PER_CALL", 2)
    tried = []

    def damaged(file_id, token):
        tried.append(file_id)
        raise FileUnreadableError("The PDF is damaged.")

    files.text = damaged
    history.messages = [make_message(1, "a", files=tuple(FileRef(f"F{n}", f"f{n}.pdf", "pdf", 5) for n in range(1, 6)))]

    result = analyze(service, include_files=True)

    assert tried == ["F1", "F2"]
    assert [name for name, _ in result.files_not_read] == ["f1.pdf", "f2.pdf", "f3.pdf", "f4.pdf", "f5.pdf"]
    assert all("at most 2 files" in why for _, why in result.files_not_read[2:])
