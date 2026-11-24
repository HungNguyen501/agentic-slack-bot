from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from channel_analysis.core.errors import ChannelAccessError
from channel_analysis.core.models import ChannelMessage
from channel_analysis.infra.slack_history import SlackChannelHistory

TOKEN = "xoxb-test"
CHANNEL = "C0123456789"


@pytest.fixture
def slack():
    with patch("channel_analysis.infra.slack_history.slack", autospec=True) as connector:
        connector.get_workspace_url.return_value = "https://acme.slack.com/"
        yield connector


def api_error(code, retry_after=None, **extra):
    response = MagicMock()
    response.get.side_effect = {"error": code, **extra}.get
    response.headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return SlackApiError("boom", response)


THREAD = {"ts": "5.0", "thread_ts": "5.0", "reply_count": 2, "text": "root"}
REPLIES = [{"ts": "5.0", "text": "root"}, {"ts": "5.1", "user": "U1", "text": "r1"}, {"ts": "5.2", "user": "U2", "text": "r2"}]


@pytest.fixture
def sleep():
    with patch("channel_analysis.infra.slack_history.time.sleep", autospec=True) as fake:
        yield fake


def test_fetch_maps_messages_and_builds_a_link_from_the_workspace_url(slack):
    slack.conversations_history.return_value = (
        [{"ts": "1763888000.123456", "username": "Mail Report", "text": "hi", "reactions": [{"name": "eyes", "count": 1}]}],
        False,
    )

    batch = SlackChannelHistory().fetch(CHANNEL, 100.0, None, 50, False, TOKEN)

    slack.conversations_history.assert_called_once_with(CHANNEL, 100.0, token=TOKEN, max_messages=50, latest=None)
    assert batch.messages == [
        ChannelMessage(
            ts="1763888000.123456", author="Mail Report", text="hi", link=f"https://acme.slack.com/archives/{CHANNEL}/p1763888000123456", reactions=(("eyes", 1),)
        )
    ]
    assert batch.last_ts == "1763888000.123456"
    assert batch.exhausted is True


def test_fetch_drops_noise_but_still_reports_the_last_ts_and_more_to_read(slack):
    slack.conversations_history.return_value = ([{"ts": "2.0", "subtype": "channel_join"}], True)

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, "9.0", 1, False, TOKEN)

    assert batch.messages == []
    assert (batch.last_ts, batch.exhausted) == ("2.0", False)
    assert slack.conversations_history.call_args.kwargs["latest"] == "9.0"


def test_fetch_with_no_messages_has_no_last_ts(slack):
    slack.conversations_history.return_value = ([], False)

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 1, False, TOKEN)

    assert (batch.messages, batch.last_ts, batch.exhausted) == ([], None, True)


def test_fetch_reads_replies_only_of_threads_and_only_when_asked(slack):
    slack.conversations_history.return_value = (
        [{"ts": "5.0", "thread_ts": "5.0", "reply_count": 2, "text": "root"}, {"ts": "4.0", "text": "no thread"}],
        False,
    )
    slack.conversations_replies.return_value = [
        {"ts": "5.0", "text": "root"},
        {"ts": "5.1", "user": "U1", "text": "r1"},
        {"ts": "5.2", "text": "r2", "subtype": "channel_join"},
    ]

    with_replies = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)
    slack.conversations_replies.reset_mock()
    without = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, False, TOKEN)

    root = with_replies.messages[0]
    assert [(r.author, r.text) for r in root.replies] == [("<@U1>", "r1")]
    assert root.reply_count == 2
    assert with_replies.messages[1].replies == ()
    slack.conversations_replies.assert_not_called()
    assert without.messages[0].replies == ()


def test_the_workspace_url_is_asked_once_per_token(slack):
    slack.conversations_history.return_value = ([], False)
    history = SlackChannelHistory()

    history.fetch(CHANNEL, 0.0, None, 1, False, TOKEN)
    history.fetch(CHANNEL, 0.0, None, 1, False, TOKEN)

    slack.get_workspace_url.assert_called_once_with(TOKEN)


@pytest.mark.parametrize(
    "code, expected",
    [("not_in_channel", "not_in_channel"), ("channel_not_found", "channel_not_found"), ("is_archived", "is_archived"), ("ratelimited", "ratelimited")],
)
def test_slack_refusals_become_access_errors(slack, code, expected):
    slack.conversations_history.side_effect = api_error(code)

    with pytest.raises(ChannelAccessError) as raised:
        SlackChannelHistory().fetch(CHANNEL, 0.0, None, 1, False, TOKEN)

    assert raised.value.code == expected


def test_a_missing_scope_names_what_slack_says_it_needed(slack):
    slack.conversations_history.side_effect = api_error("missing_scope", needed="groups:history")

    with pytest.raises(ChannelAccessError, match="groups:history"):
        SlackChannelHistory().fetch(CHANNEL, 0.0, None, 1, False, TOKEN)


def test_another_slack_error_propagates(slack):
    slack.conversations_history.side_effect = api_error("internal_error")

    with pytest.raises(SlackApiError):
        SlackChannelHistory().fetch(CHANNEL, 0.0, None, 1, False, TOKEN)


@pytest.mark.parametrize(
    "raw, private, dm",
    [({"is_private": True}, True, False), ({"is_group": True}, True, False), ({"is_im": True}, False, True), ({"is_mpim": True}, False, True), ({}, False, False)],
)
def test_channel_info_reads_privacy_and_dm_flags(slack, raw, private, dm):
    slack.get_channel_info.return_value = {"name": "mail", "is_member": True, "is_archived": False} | raw

    info = SlackChannelHistory().get_channel_info(CHANNEL, TOKEN)

    assert (info.name, info.is_member, info.is_private, info.is_dm) == ("mail", True, private, dm)


def test_find_channel_id_and_membership_pass_through(slack):
    slack.find_channel_id.return_value = CHANNEL
    slack.is_channel_member.return_value = None
    history = SlackChannelHistory()

    assert history.find_channel_id("mail", TOKEN) == CHANNEL
    assert history.is_member(CHANNEL, "U1", TOKEN, 10) is None
    slack.is_channel_member.assert_called_once_with(CHANNEL, "U1", token=TOKEN, max_pages=10)


def test_can_read_files_is_false_for_a_missing_scope_and_other_slack_refusals_are_access_errors(slack):
    history = SlackChannelHistory()
    slack.list_files.return_value = []
    assert history.can_read_files(CHANNEL, TOKEN) is True

    slack.list_files.side_effect = api_error("missing_scope", needed="files:read")
    assert history.can_read_files(CHANNEL, TOKEN) is False

    slack.list_files.side_effect = api_error("ratelimited")
    with pytest.raises(ChannelAccessError) as raised:
        history.can_read_files(CHANNEL, TOKEN)
    assert raised.value.code == "ratelimited"

    slack.list_files.side_effect = api_error("internal_error")
    with pytest.raises(SlackApiError):
        history.can_read_files(CHANNEL, TOKEN)


def test_a_channel_name_is_looked_up_once_per_token_and_a_miss_is_not_remembered(slack):
    slack.find_channel_id.side_effect = [CHANNEL, None, "C9999999999"]
    history = SlackChannelHistory()

    assert history.find_channel_id("mail", TOKEN) == CHANNEL
    assert history.find_channel_id("mail", TOKEN) == CHANNEL
    assert slack.find_channel_id.call_count == 1
    assert history.find_channel_id("other", TOKEN) is None
    assert history.find_channel_id("other", TOKEN) == "C9999999999"


def test_a_rate_limited_thread_is_retried_once_after_the_wait_slack_asks_for(slack, sleep):
    slack.conversations_history.return_value = ([THREAD], False)
    slack.conversations_replies.side_effect = [api_error("ratelimited", retry_after=7), REPLIES]

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    sleep.assert_called_once_with(7.0)
    assert [r.text for r in batch.messages[0].replies] == ["r1", "r2"]
    assert batch.messages[0].omitted_replies == 0


def test_the_wait_for_a_rate_limit_is_capped(slack, sleep):
    slack.conversations_history.return_value = ([THREAD], False)
    slack.conversations_replies.side_effect = [api_error("ratelimited", retry_after=600), REPLIES]

    SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    sleep.assert_called_once_with(30.0)


def test_a_thread_that_stays_rate_limited_is_shown_with_its_reply_count_and_the_page_still_returns(slack, sleep):
    slack.conversations_history.return_value = ([THREAD, {"ts": "4.0", "text": "plain"}], False)
    slack.conversations_replies.side_effect = api_error("ratelimited")

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    thread, plain = batch.messages
    assert (thread.reply_count, thread.replies, thread.omitted_replies) == (2, (), 2)
    assert plain.text == "plain"
    assert slack.conversations_replies.call_count == 2


def test_once_slack_still_refuses_after_a_wait_the_other_threads_of_the_batch_are_not_asked(slack, sleep):
    slack.conversations_history.return_value = ([THREAD | {"ts": f"{n}.0", "thread_ts": f"{n}.0"} for n in range(50, 0, -1)], False)
    slack.conversations_replies.side_effect = api_error("ratelimited", retry_after=5)

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    assert slack.conversations_replies.call_count == 2
    sleep.assert_called_once_with(5.0)
    assert all(m.replies == () and m.omitted_replies == 2 for m in batch.messages)


def test_the_waits_of_one_fetch_share_a_budget_of_30_seconds(slack, sleep):
    slack.conversations_history.return_value = ([THREAD | {"ts": f"{n}.0", "thread_ts": f"{n}.0"} for n in range(4, 0, -1)], False)
    # The first two threads are limited once and then answered, so each waits; the budget runs out at the third.
    slack.conversations_replies.side_effect = [
        api_error("ratelimited", retry_after=20), REPLIES, api_error("ratelimited", retry_after=20), REPLIES, api_error("ratelimited", retry_after=20),
    ]  # fmt: skip

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    assert [c.args[0] for c in sleep.call_args_list] == [20.0, 10.0]
    assert sum(c.args[0] for c in sleep.call_args_list) <= 30.0
    assert [bool(m.replies) for m in batch.messages] == [True, True, False, False]
    assert [m.omitted_replies for m in batch.messages] == [0, 0, 2, 2]
    assert slack.conversations_replies.call_count == 5


def test_each_fetch_gets_its_own_wait_budget(slack, sleep):
    slack.conversations_history.return_value = ([THREAD], False)
    slack.conversations_replies.side_effect = [api_error("ratelimited", retry_after=25), REPLIES] * 2
    history = SlackChannelHistory()

    history.fetch(CHANNEL, 0.0, None, 50, True, TOKEN)
    history.fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    assert [c.args[0] for c in sleep.call_args_list] == [25.0, 25.0]


@pytest.mark.parametrize(
    "error", [api_error("channel_not_found"), api_error("thread_not_found"), api_error("internal_error")], ids=["refused", "unknown-thread", "unexpected"]
)
def test_a_thread_that_cannot_be_read_is_shown_without_replies_and_the_page_still_returns(slack, sleep, error):
    slack.conversations_history.return_value = ([THREAD, {"ts": "4.0", "text": "plain"}], False)
    slack.conversations_replies.side_effect = error

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    thread, plain = batch.messages
    assert (thread.replies, thread.omitted_replies, plain.text) == ((), 2, "plain")
    sleep.assert_not_called()


def test_one_thread_that_cannot_be_read_does_not_stop_the_others_from_being_read(slack, sleep):
    slack.conversations_history.return_value = ([THREAD, THREAD | {"ts": "4.0", "thread_ts": "4.0"}], False)
    slack.conversations_replies.side_effect = [api_error("thread_not_found"), REPLIES]

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    assert [bool(m.replies) for m in batch.messages] == [False, True]


def test_a_thread_is_read_up_to_a_bound_and_the_rest_is_counted_as_not_shown(slack):
    slack.conversations_history.return_value = ([THREAD | {"reply_count": 500}], False)
    slack.conversations_replies.return_value = [{"ts": "5.0", "text": "root"}] + [{"ts": f"6.{n}", "text": f"r{n}"} for n in range(100)]

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 50, True, TOKEN)

    assert slack.conversations_replies.call_args.kwargs["max_messages"] == 101
    assert (len(batch.messages[0].replies), batch.messages[0].omitted_replies) == (100, 400)


def test_fetch_reports_how_many_messages_slack_returned_noise_included(slack):
    slack.conversations_history.return_value = ([{"ts": "3.0", "subtype": "channel_join"}, {"ts": "2.0", "text": "real"}], True)

    batch = SlackChannelHistory().fetch(CHANNEL, 0.0, None, 2, False, TOKEN)

    assert (len(batch.messages), batch.fetched) == (1, 2)
