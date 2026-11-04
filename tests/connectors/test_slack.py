from unittest.mock import MagicMock, patch

import pytest

from connectors import slack
from connectors.slack import split_message

MAX_LEN = 40


def test_split_message_returns_short_text_unchanged():
    assert split_message("hello", max_len=MAX_LEN) == ["hello"]


def test_split_message_splits_plain_text_on_line_boundaries():
    text = "\n".join(["a" * 15] * 4)

    chunks = split_message(text, max_len=MAX_LEN)

    assert chunks == ["a" * 15 + "\n" + "a" * 15, "a" * 15 + "\n" + "a" * 15]


@pytest.mark.parametrize("prefix", ["", "short line\n"], ids=["alone", "after-content"])
def test_split_message_cuts_a_line_longer_than_max_len(prefix):
    chunks = split_message(prefix + "x" * 100, max_len=MAX_LEN)

    assert all(len(c) <= MAX_LEN for c in chunks)
    assert "".join(chunks).count("x") == 100


def test_split_message_keeps_all_characters_of_an_oversized_line():
    chunks = split_message("x" * 100, max_len=MAX_LEN)

    assert "".join(chunks) == "x" * 100


def test_split_message_reserves_room_for_the_closing_fence():
    text = "```\n" + "\n".join(["a" * 11] * 5) + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert len(chunks) > 1
    assert all(len(c) <= MAX_LEN for c in chunks)


def test_split_message_reopens_code_block_with_language_tag():
    text = "```sql\n" + "\n".join(["select 1 from t"] * 6) + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert all(c.startswith("```sql") and c.endswith("```") for c in chunks)
    assert all(len(c) <= MAX_LEN for c in chunks)


def test_split_message_oversized_line_inside_code_block_stays_fenced():
    text = "```\n" + "y" * 100 + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert all(len(c) <= MAX_LEN for c in chunks)
    assert all(c.startswith("```") and c.endswith("```") for c in chunks)
    assert "".join(c.removeprefix("```\n").removesuffix("\n```") for c in chunks).replace("```", "").replace("\n", "") == "y" * 100


def test_split_message_treats_one_line_code_block_as_balanced():
    text = "x\n```x```\nhello world\nmore text"

    chunks = split_message(text, max_len=20)

    assert chunks == ["x\n```x```", "hello world", "more text"]


def page(messages, next_cursor=""):
    return {"messages": messages, "response_metadata": {"next_cursor": next_cursor}}


def test_conversations_replies_follows_the_cursor_until_the_last_page():
    client = MagicMock()
    client.conversations_replies.side_effect = [page([{"ts": "1"}], "c2"), page([{"ts": "2"}])]

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages = slack.conversations_replies("C1", "1", token="t")

    assert messages == [{"ts": "1"}, {"ts": "2"}]
    assert [c.kwargs["cursor"] for c in client.conversations_replies.call_args_list] == [None, "c2"]


def test_conversations_history_asks_for_messages_after_oldest():
    client = MagicMock()
    client.conversations_history.return_value = page([{"ts": "9"}])

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages, truncated = slack.conversations_history("C1", 1234.5, token="t", max_messages=10)

    client.conversations_history.assert_called_once_with(channel="C1", oldest="1234.500000", limit=200, cursor=None)
    assert messages == [{"ts": "9"}]
    assert truncated is False


def test_conversations_history_stops_at_max_messages_and_says_it_left_some_unread():
    client = MagicMock()
    client.conversations_history.return_value = page([{"ts": str(n)} for n in range(5)], "more")

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages, truncated = slack.conversations_history("C1", 0, token="t", max_messages=3)

    assert [m["ts"] for m in messages] == ["0", "1", "2"]
    assert truncated is True
    assert client.conversations_history.call_count == 1


def test_conversations_history_is_truncated_when_the_cap_is_hit_exactly_with_more_pages_to_read():
    client = MagicMock()
    client.conversations_history.return_value = page([{"ts": "1"}, {"ts": "2"}], "more")

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages, truncated = slack.conversations_history("C1", 0, token="t", max_messages=2)

    assert len(messages) == 2
    assert truncated is True
    assert client.conversations_history.call_count == 1  # no further page is fetched once the cap is reached


def test_conversations_history_is_not_truncated_when_it_hits_the_cap_on_the_last_page():
    client = MagicMock()
    client.conversations_history.return_value = page([{"ts": "1"}, {"ts": "2"}])

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages, truncated = slack.conversations_history("C1", 0, token="t", max_messages=2)

    assert len(messages) == 2
    assert truncated is False


def test_conversations_history_cuts_an_oversized_last_page_and_says_so():
    client = MagicMock()
    client.conversations_history.return_value = page([{"ts": str(n)} for n in range(5)])

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages, truncated = slack.conversations_history("C1", 0, token="t", max_messages=3)

    assert len(messages) == 3
    assert truncated is True


def test_get_permalink_returns_the_link():
    client = MagicMock()
    client.chat_getPermalink.return_value = {"permalink": "https://slack.test/p1"}

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.get_permalink("C1", "1.0", token="t") == "https://slack.test/p1"

    client.chat_getPermalink.assert_called_once_with(channel="C1", message_ts="1.0")


def test_get_bot_user_id_asks_slack_who_the_token_belongs_to():
    client = MagicMock()
    client.auth_test.return_value = {"user_id": "UBOT123"}

    with patch("connectors.slack.WebClient", autospec=True, return_value=client) as web_client:
        assert slack.get_bot_user_id("xoxb-1") == "UBOT123"

    web_client.assert_called_once_with(token="xoxb-1")
