from unittest.mock import create_autospec

import pytest

from bots.core.models import BotConfig
from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.models import (
    AccessProblem,
    Analysis,
    ChannelAccess,
    ChannelMessage,
    ChannelSlice,
    ExtractionField,
    ExtractionSpec,
    FailedChunk,
    FilePage,
    FileRef,
    GroupCount,
    Record,
)
from channel_analysis.core.service import ChannelAnalysisService
from channel_analysis.presentation import tools

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")
START, END = 1_799_000_000.0, 1_800_000_000.0


@pytest.fixture
def service():
    return create_autospec(ChannelAnalysisService, instance=True)


def run(service, name, args, user_id="UADMIN"):
    return tools.dispatch_channel_analysis_tool(name, args, user_id, BOT, service)


def page(messages=(), next_before=None, terms=(), scanned=None):
    return ChannelSlice("C1", START, END, terms, tuple(messages), len(messages) if scanned is None else scanned, next_before)


def test_check_says_ok_when_there_is_no_problem(service):
    service.check_access.return_value = ChannelAccess("C1", "mail", ())

    reply = run(service, "check_channel_access", {"channel": "#mail"})

    assert reply == "Access check for #mail (C1): OK. I can read it and you may see it."
    service.check_access.assert_called_once_with(BOT, "#mail", "UADMIN")


def test_check_lists_every_problem_and_says_to_stop_when_one_blocks(service):
    service.check_access.return_value = ChannelAccess(
        "C1", "mail", (AccessProblem("not_in_channel", "I'm not in it."), AccessProblem("files_scope", "No files.", blocking=False))
    )

    reply = run(service, "check_channel_access", {"channel": "C1"})

    assert "NOT OK" in reply
    assert "- BLOCKING: I'm not in it." in reply
    assert "- warning: No files." in reply


def test_check_with_only_a_warning_is_ok_to_read_messages(service):
    service.check_access.return_value = ChannelAccess("C1", "mail", (AccessProblem("files_scope", "No files.", blocking=False),), empty_window=True)

    reply = run(service, "check_channel_access", {"channel": "C1"})

    assert "OK to read messages, with a warning" in reply
    assert "no messages in the last 30 days" in reply


def test_a_core_error_is_returned_as_the_reply(service):
    service.check_access.side_effect = ChannelAnalysisError("That is a direct message.")

    assert run(service, "check_channel_access", {"channel": "D1"}) == "That is a direct message."


def test_read_passes_only_the_arguments_it_was_given_so_the_use_case_owns_the_defaults(service):
    service.read.return_value = page()

    run(service, "read_channel_messages", {"channel": "C1", "include_replies": "no", "before": ""}, user_id=None)

    service.read.assert_called_once_with(BOT, "C1", None)


def test_read_passes_every_argument_through(service):
    service.read.return_value = page()
    args = {"channel": "C1", "lookback_days": "7", "end_days_ago": 7, "contains": ["a", "b"], "include_replies": False, "before": "1.5"}

    run(service, "read_channel_messages", args)

    service.read.assert_called_once_with(BOT, "C1", "UADMIN", lookback_days=7, end_days_ago=7, contains=("a", "b"), include_replies=False, before="1.5")


def test_a_single_string_for_contains_is_taken_as_one_term(service):
    service.read.return_value = page()

    run(service, "read_channel_messages", {"channel": "C1", "contains": "bridgewell"})

    assert service.read.call_args.kwargs["contains"] == ("bridgewell",)


@pytest.mark.parametrize(
    "args, message",
    [
        ({"lookback_days": "soon"}, "lookback_days"),
        ({"lookback_days": "--1"}, "lookback_days"),
        ({"lookback_days": [7]}, "lookback_days"),
        ({"end_days_ago": True}, "end_days_ago"),
        ({"contains": [1]}, "contains"),
        ({"contains": 5}, "contains"),
    ],
)
def test_a_badly_shaped_argument_is_refused_before_the_use_case(service, args, message):
    reply = run(service, "read_channel_messages", {"channel": "C1"} | args)

    assert message in reply
    service.read.assert_not_called()


def test_a_page_shows_time_author_link_files_reactions_replies_and_the_counts(service):
    reply_message = ChannelMessage(ts="1799990100.000000", author="<@U1>", text="thanks")
    message = ChannelMessage(
        ts="1799990000.000000", author="Mail Report", text="agility_po_costs: skipped\nno records", link="https://acme.slack.com/archives/C1/p1",
        reactions=(("white_check_mark", 2),), files=(FileRef("F1", "rows.csv", "csv", 84 * 1024),), reply_count=3, replies=(reply_message,), omitted_replies=2,
    )  # fmt: skip
    service.read.return_value = page([message], terms=("bridgewell_com",), scanned=120)

    reply = run(service, "read_channel_messages", {"channel": "C1"})

    assert reply.splitlines()[0] == "Channel C1 · window 2027-01-03 18:13 UTC to 2027-01-15 08:00 UTC · newest first"
    assert "This page: 1 messages (scanned 120, matched any of: bridgewell_com)" in reply
    assert "[2027-01-15 05:13 UTC] Mail Report · https://acme.slack.com/archives/C1/p1" in reply
    assert "agility_po_costs: skipped\nno records" in reply
    assert "[file F1: rows.csv, csv, 84 KB]" in reply
    assert "reactions: :white_check_mark:×2 · replies: 3" in reply
    assert "  ↳ [2027-01-15 05:15 UTC] <@U1>\n    thanks" in reply
    assert "(2 more replies not shown)" in reply
    assert reply.endswith("End of window: that was every matching message.")


def test_a_reply_shows_its_files_and_reactions_and_a_reply_with_only_a_file_is_not_empty(service):
    with_file = ChannelMessage(ts="1799990100.000000", author="<@U1>", text="", files=(FileRef("F7", "late.csv", "csv", 3 * 1024),), reactions=(("eyes", 1),))
    root = ChannelMessage(ts="1799990000.000000", author="Mail Report", text="root", reply_count=1, replies=(with_file,))
    service.read.return_value = page([root])

    reply = run(service, "read_channel_messages", {"channel": "C1"})

    assert "  ↳ [2027-01-15 05:15 UTC] <@U1>\n    (no text)\n    [file F7: late.csv, csv, 3 KB]\n    reactions: :eyes:×1" in reply


def test_a_multi_line_reply_stays_indented_under_its_header(service):
    reply_message = ChannelMessage(ts="1799990100.000000", author="<@U1>", text="line one\nline two")
    service.read.return_value = page([ChannelMessage(ts="1799990000.000000", author="A", text="root", replies=(reply_message,))])

    reply = run(service, "read_channel_messages", {"channel": "C1"})

    assert "  ↳ [2027-01-15 05:15 UTC] <@U1>\n    line one\n    line two" in reply


def test_an_unfinished_window_says_how_to_continue(service):
    service.read.return_value = page(next_before="1799990000.123456")

    reply = run(service, "read_channel_messages", {"channel": "C1"})

    assert 'before="1799990000.123456"' in reply
    assert "End of window" not in reply


def test_read_file_formats_the_page_and_the_next_offset(service):
    service.read_file.return_value = FilePage("rows.csv", "csv", 2048, "id,n\n1,a\n", 0, 9, 30)

    reply = run(service, "read_channel_file", {"channel": "C1", "file_id": "F1", "offset": "0"})

    service.read_file.assert_called_once_with(BOT, "C1", "UADMIN", "F1", 0)
    assert reply.splitlines()[0] == "File rows.csv (csv, 2 KB) · characters 0 to 9 of 30"
    assert "id,n\n1,a" in reply
    assert "offset=9" in reply


def test_the_last_file_page_says_end_of_file(service):
    service.read_file.return_value = FilePage("rows.csv", "csv", 10, "x", 9, None, 10)

    assert run(service, "read_channel_file", {"channel": "C1", "file_id": "F1"}).endswith("End of file.")


def test_a_bad_offset_is_refused_in_words(service):
    reply = run(service, "read_channel_file", {"channel": "C1", "file_id": "F1", "offset": "--1"})

    assert "`offset` must be a whole number" in reply
    service.read_file.assert_not_called()


def test_read_file_without_an_id_asks_for_it(service):
    assert "file ID" in run(service, "read_channel_file", {"channel": "C1"})
    service.read_file.assert_not_called()


def test_an_unexpected_failure_is_reported_not_raised(service):
    service.read.side_effect = RuntimeError("boom")

    assert run(service, "read_channel_messages", {"channel": "C1"}) == "Error executing read_channel_messages: boom"


def test_an_unknown_tool_is_named(service):
    assert run(service, "read_everything", {}) == "Unknown channel analysis tool: read_everything"


# --- analyze_channel

FIELDS = [{"name": "table", "description": "the table"}, {"name": "status", "description": "what happened", "allowed_values": ["skipped", "failed"]}]
ANALYZE_ARGS = {"channel": "C1", "instruction": "one record per table row", "fields": FIELDS}


def analysis(**overrides):
    fields = {
        "channel_id": "C1", "window_start": START, "window_end": END, "terms": (), "fields": ("table", "status"), "group_by": ("table", "status"),
        "messages_read": 34, "scanned": 120, "chunks": 2, "failed_chunks": (), "files_not_read": (), "records": 19, "rejected": 3,
        "groups": (GroupCount(("agility_po_costs", "skipped"), 11, 11, 8, ("https://x/p1", "https://x/p2")),), "groups_omitted": 0, "records_omitted": 0,
        "sample": (Record("1799990000.000000", "https://x/p1", {"table": "agility_po_costs", "status": "skipped"}),), "next_before": None,
    } | overrides  # fmt: skip
    return Analysis(**fields)


def test_an_analysis_passes_the_spec_the_grouping_and_the_window_arguments_to_the_use_case(service):
    service.analyze.return_value = analysis()
    args = ANALYZE_ARGS | {"group_by": ["table"], "include_files": True, "lookback_days": 7, "contains": ["bridgewell"], "before": "1.5@2.5"}

    run(service, "analyze_channel", args)

    spec = ExtractionSpec("one record per table row", (ExtractionField("table", "the table"), ExtractionField("status", "what happened", ("skipped", "failed"))))
    service.analyze.assert_called_once_with(
        BOT, "C1", "UADMIN", spec, group_by=("table",), include_files=True, lookback_days=7, contains=("bridgewell",), before="1.5@2.5"
    )


def test_files_are_only_included_when_asked_with_a_real_true(service):
    service.analyze.return_value = analysis()

    run(service, "analyze_channel", ANALYZE_ARGS | {"include_files": "yes"})

    assert service.analyze.call_args.kwargs["include_files"] is False


def test_the_result_shows_what_was_read_the_counts_the_sample_and_that_the_window_is_done(service):
    service.analyze.return_value = analysis(terms=("bridgewell_com",))

    reply = run(service, "analyze_channel", ANALYZE_ARGS)

    lines = reply.splitlines()
    assert lines[0] == "Analysis of channel C1 · window 2027-01-03 18:13 UTC to 2027-01-15 08:00 UTC"
    assert lines[1] == "Read 34 messages in 2 chunks (scanned 120, matched any of: bridgewell_com). Extracted 19 records; rejected 3."
    assert lines[2] == "Fields: table, status · counted by: table, status"
    assert "Chunks that failed: none" in reply and "Files not read: none" in reply
    assert "- table=agility_po_costs · status=skipped: 11 · 11 · 8 · https://x/p1 https://x/p2" in reply
    assert "- [2027-01-15 05:13 UTC] https://x/p1: table=agility_po_costs, status=skipped" in reply
    assert reply.endswith("End of window: that was every matching message.")


def test_without_group_by_the_total_is_one_line(service):
    service.analyze.return_value = analysis(group_by=(), groups=(GroupCount((), 19, 12, 9, ()),))

    reply = run(service, "analyze_channel", ANALYZE_ARGS)

    assert "counted by: nothing (one overall total)" in reply
    assert "- all records: 19 · 12 · 9" in reply


def test_failed_chunks_files_not_read_and_unreliable_are_said_plainly(service):
    failed = (FailedChunk("1799980000.000000", "1799990000.000000", 5, "The extraction model failed (APITimeoutError)."),)
    service.analyze.return_value = analysis(failed_chunks=failed, chunks=1, files_not_read=(("scan.pdf", "no text layer"), ("big.csv", "larger than 5 MB")))

    reply = run(service, "analyze_channel", ANALYZE_ARGS)

    assert "NOT in the counts" in reply
    assert "- 2027-01-15 02:26 UTC to 2027-01-15 05:13 UTC, 5 messages: The extraction model failed (APITimeoutError)." in reply
    assert "Files not read: scan.pdf (no text layer); big.csv (larger than 5 MB)" in reply
    assert "UNRELIABLE" in reply


def test_an_unfinished_window_says_how_to_continue_and_how_to_add_the_counts_up(service):
    service.analyze.return_value = analysis(next_before="1799990000.123456@1799000000.000000", groups_omitted=2, records_omitted=5)

    reply = run(service, "analyze_channel", ANALYZE_ARGS)

    assert 'analyze_channel again with before="1799990000.123456@1799000000.000000"' in reply
    assert "add them up across calls" in reply and "(2 more groups: 5 records)" in reply
    assert "End of window" not in reply


def test_an_analysis_with_no_records_says_so(service):
    service.analyze.return_value = analysis(groups=(), sample=(), records=0, messages_read=0, chunks=0)

    reply = run(service, "analyze_channel", ANALYZE_ARGS)

    assert "(no records)" in reply and "Sample records" not in reply


@pytest.mark.parametrize(
    "args, message",
    [
        ({"instruction": 5}, "instruction"),
        ({"instruction": None}, "instruction"),
        ({"fields": "table"}, "fields"),
        ({"fields": ["table"]}, "fields"),
        ({"fields": [{"description": "no name"}]}, "name"),
        ({"fields": [{"name": 5}]}, "name"),
        ({"fields": [{"name": "t", "description": 5}]}, "name"),
        ({"fields": [{"name": "t", "allowed_values": "skipped"}]}, "allowed_values"),
        ({"group_by": "table"}, "group_by"),
        ({"group_by": [1]}, "group_by"),
        ({"lookback_days": "soon"}, "lookback_days"),
    ],
)
def test_a_badly_shaped_analysis_request_is_refused_in_words_before_the_use_case(service, args, message):
    reply = run(service, "analyze_channel", ANALYZE_ARGS | args)

    assert message in reply
    service.analyze.assert_not_called()


def test_the_use_cases_refusal_is_the_reply(service):
    service.analyze.side_effect = ChannelAnalysisError("`group_by` names `nope`, which is not one of the fields (table, status).")

    assert "not one of the fields" in run(service, "analyze_channel", ANALYZE_ARGS)
