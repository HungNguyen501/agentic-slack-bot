import logging
import re
import time

import pytest

from channel_analysis.core import limits
from channel_analysis.core.errors import ChannelAccessError, ChannelAnalysisError, ExtractionError, ExtractionShapeError, FileUnreadableError
from channel_analysis.core.models import ExtractionField, ExtractionSpec, FileRef
from tests.support.channel_analysis import BOT, CHANNEL, WINDOW_START, make_info, make_message, resume_at, ts_ago

ADMIN = "UADMIN"
SPEC = ExtractionSpec(
    "one record per table row of the report",
    (ExtractionField("table", "the table"), ExtractionField("status", "what happened", ("skipped", "failed", "loaded"))),
)


def analyze(service, **kwargs):
    return service.analyze(BOT, kwargs.pop("channel", CHANNEL), kwargs.pop("requester", ADMIN), kwargs.pop("spec", SPEC), **kwargs)


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
    monkeypatch.setattr(limits, "CHUNK_CHAR_BUDGET", budget)
    monkeypatch.setattr(limits, "MAX_CHUNKS_PER_CALL", chunks)


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
    monkeypatch.setattr(limits, "SAMPLE_RECORDS", 2)
    history.messages = [make_message(n, f"m{n}") for n in (1, 2, 3)]
    extractor.respond = one_record_per_item()

    result = analyze(service)

    assert [r.ts for r in result.sample] == [ts_ago(1), ts_ago(2)]
    assert result.records == 3


def test_groups_past_the_cap_are_summarized_not_listed(service, history, extractor, monkeypatch):
    monkeypatch.setattr(limits, "MAX_GROUPS_LISTED", 2)
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
    monkeypatch.setattr(limits, "ITEM_CHAR_LIMIT", 90)
    files.texts["F1"] = "line\n" * 100
    history.messages = [make_message(1, "report", files=(FileRef("F1", "log.txt", "text", 500),)), make_message(2, "older")]
    extractor.respond = one_record_per_item()

    result = analyze(service, include_files=True)

    assert result.messages_read == 1
    assert result.next_before == resume_at(1)


def test_a_scan_that_stops_at_its_cap_returns_a_cursor(service, history, extractor, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SCAN_PER_CALL_ANALYSIS_WITH_REPLIES", 3)
    monkeypatch.setattr(limits, "FETCH_BATCH_WITH_REPLIES", 2)
    history.messages = [make_message(n, "noise") for n in range(1, 9)]

    result = analyze(service, contains=("needle",))

    assert result.messages_read == 0 and result.chunks == 0
    assert result.next_before == resume_at(4)


def test_the_extraction_scan_cap_is_bigger_than_a_page_reads(service, history, extractor):
    history.messages = [make_message(n, "x") for n in range(1, 4)]

    analyze(service, include_replies=False)

    assert limits.MAX_SCAN_PER_CALL_ANALYSIS > limits.MAX_SCAN_PER_CALL
    assert history.fetches[0]["limit"] == limits.FETCH_BATCH


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


# --- files


def test_files_are_not_read_unless_asked(service, history, extractor, files):
    files.texts["F1"] = "never read"
    history.messages = [make_message(1, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]

    result = analyze(service)

    assert "never read" not in extractor.calls[0][1] and result.files_not_read == ()


def test_a_file_becomes_more_items_of_its_message(service, history, extractor, files):
    files.texts["F1"] = "table,status\nagility_po_costs,skipped\n"
    history.messages = [make_message(1, "report", files=(FileRef("F1", "rows.csv", "csv", 10),))]
    extractor.respond = one_record_per_item()

    result = analyze(service, include_files=True)

    user = extractor.calls[0][1]
    assert "agility_po_costs,skipped" in user and "file rows.csv (part 1)" in user
    # the message and its file both say the same row, and one message counts a row once
    assert result.messages_read == 1 and result.records == 1 and result.groups[0].messages == 1


@pytest.mark.parametrize(
    "ref, text_error, reason",
    [
        (FileRef("F1", "huge.csv", "csv", limits.FILE_MAX_BYTES + 1), None, "larger than 5 MB"),
        (FileRef("F1", "scan.pdf", "pdf", 10), FileUnreadableError("That PDF has no text layer."), "no text layer"),
        (FileRef("F1", "x.csv", "csv", 10), ChannelAnalysisError("I can't find that file."), "can't find that file"),
    ],
    ids=["too-big", "unreadable", "missing"],
)
def test_a_file_that_cannot_be_read_is_listed_with_the_reason_and_the_message_is_still_analyzed(service, history, extractor, files, ref, text_error, reason):
    history.messages = [make_message(1, "report", files=(ref,))]
    files.text = lambda file_id, token: (_ for _ in ()).throw(text_error) if text_error else "x"

    result = analyze(service, include_files=True)

    assert [(name, why) for name, why in result.files_not_read] == [(ref.name, reason)] or reason in result.files_not_read[0][1]
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
    monkeypatch.setattr(limits, "MAX_FILES_PER_CALL", 2)
    for n in (1, 2, 3):
        files.texts[f"F{n}"] = f"file {n}"
    history.messages = [make_message(1, "a", files=tuple(FileRef(f"F{n}", f"f{n}.txt", "text", 5) for n in (1, 2, 3)))]

    result = analyze(service, include_files=True)

    assert [name for name, _ in result.files_not_read] == ["f3.txt"]
    assert "at most 2 files" in result.files_not_read[0][1]


def test_a_message_adds_only_so_many_pieces_and_says_a_file_was_cut(service, history, extractor, files, monkeypatch):
    monkeypatch.setattr(limits, "ITEM_CHAR_LIMIT", 40)
    monkeypatch.setattr(limits, "MAX_ITEMS_PER_MESSAGE", 3)
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
