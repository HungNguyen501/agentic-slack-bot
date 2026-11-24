from unittest.mock import create_autospec

import pytest

from bots.core.models import BotConfig
from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.models import AccessProblem, ChannelAccess, ChannelMessage, ChannelSlice, FilePage, FileRef
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
    assert "  ↳ [2027-01-15 05:15 UTC] <@U1>: thanks" in reply
    assert "(2 more replies not shown)" in reply
    assert reply.endswith("End of window: that was every matching message.")


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
