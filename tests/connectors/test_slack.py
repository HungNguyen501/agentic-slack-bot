from unittest.mock import MagicMock, create_autospec, patch

import pytest
from redis import Redis, RedisError
from slack_sdk.errors import SlackApiError

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

    client.conversations_history.assert_called_once_with(channel="C1", oldest="1234.500000", limit=10, cursor=None)
    assert messages == [{"ts": "9"}]
    assert truncated is False


def test_conversations_history_asks_slack_for_at_most_200_per_page():
    client = MagicMock()
    client.conversations_history.return_value = page([])

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        slack.conversations_history("C1", 0, token="t", max_messages=1000)

    assert client.conversations_history.call_args.kwargs["limit"] == 200


def test_conversations_history_passes_latest_through_untouched_and_only_when_given():
    client = MagicMock()
    client.conversations_history.return_value = page([])

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        slack.conversations_history("C1", 0, token="t", max_messages=5, latest="1763900000.123456")
        slack.conversations_history("C1", 0, token="t", max_messages=5)

    first, second = client.conversations_history.call_args_list
    assert first.kwargs["latest"] == "1763900000.123456"
    assert "latest" not in second.kwargs


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


def test_add_reaction_reacts_to_the_message_as_the_bot():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        slack.add_reaction("C1", "1.5", "white_check_mark", token="xoxb-1")

    client.assert_called_once_with(token="xoxb-1")
    client.return_value.reactions_add.assert_called_once_with(channel="C1", timestamp="1.5", name="white_check_mark")


@pytest.fixture(autouse=True)
def redis():
    """A Redis that remembers keys, standing in for the shared client the connector keeps its 'no customize scope' marks in."""
    keys = {}
    client = create_autospec(Redis, instance=True)
    client.exists.side_effect = lambda key: int(key in keys)
    client.set.side_effect = lambda key, value, ex=None: keys.__setitem__(key, ex)
    client.marks = keys
    with patch("connectors.slack.redis_client", client):
        yield client


def slack_error(code):
    response = MagicMock()
    response.get.return_value = code
    return SlackApiError("boom", response)


def test_post_message_without_an_identity_sends_none_of_the_customize_arguments():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.return_value = {"ts": "9.9"}

        assert slack.post_message("C1", "hi", "1.0", token="xoxb-1") == "9.9"

    client.return_value.chat_postMessage.assert_called_once_with(channel="C1", text="hi", thread_ts="1.0", blocks=None)


def test_post_message_sends_the_name_and_icon_it_is_given():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.return_value = {"ts": "9.9"}

        slack.post_message("C1", "hi", "1.0", token="xoxb-1", username="Em Gái Mưa", icon_emoji=":cloud_with_rain:")

    client.return_value.chat_postMessage.assert_called_once_with(
        channel="C1", text="hi", thread_ts="1.0", blocks=None, username="Em Gái Mưa", icon_emoji=":cloud_with_rain:"
    )


def test_post_message_posts_as_the_bot_when_the_app_lacks_the_customize_scope():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        post = client.return_value.chat_postMessage
        post.side_effect = [slack_error("missing_scope"), {"ts": "9.9"}]

        assert slack.post_message("C1", "hi", "1.0", token="xoxb-1", username="Anna") == "9.9"

    assert post.call_args_list[1].kwargs == {"channel": "C1", "text": "hi", "thread_ts": "1.0", "blocks": None}


@pytest.mark.parametrize("identity", [{}, {"username": "Anna"}], ids=["no-identity", "identity"])
def test_post_message_raises_other_slack_errors(identity):
    with patch("connectors.slack.WebClient", autospec=True) as client, pytest.raises(SlackApiError):
        client.return_value.chat_postMessage.side_effect = slack_error("channel_not_found" if identity else "missing_scope")
        slack.post_message("C1", "hi", token="xoxb-1", **identity)


def test_post_message_does_not_retry_a_missing_scope_when_no_identity_was_asked_for():
    with patch("connectors.slack.WebClient", autospec=True) as client, pytest.raises(SlackApiError):
        client.return_value.chat_postMessage.side_effect = slack_error("missing_scope")
        slack.post_message("C1", "hi", token="xoxb-1")

    client.return_value.chat_postMessage.assert_called_once()


def test_after_a_missing_scope_the_same_app_posts_as_the_bot_without_asking_again():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        post = client.return_value.chat_postMessage
        post.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}, {"ts": "1.2"}, {"ts": "1.3"}]

        slack.post_message("C1", "one", token="xoxb-1", username="Anna")
        slack.post_message("C1", "two", token="xoxb-1", username="Anna", icon_emoji=":star:")
        slack.post_message("C1", "three", token="xoxb-1", username="Anna")

    assert post.call_count == 4  # one refused attempt and its retry, then one call per message
    assert all("username" not in call.kwargs and "icon_emoji" not in call.kwargs for call in post.call_args_list[1:])


def test_a_missing_scope_is_logged_once_per_app(caplog):
    with patch("connectors.slack.WebClient", autospec=True) as client, caplog.at_level("WARNING", logger="connectors.slack"):
        client.return_value.chat_postMessage.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}, {"ts": "1.2"}]

        slack.post_message("C1", "one", token="xoxb-1", username="Anna")
        slack.post_message("C1", "two", token="xoxb-1", username="Anna")

    assert caplog.text.count("lacks chat:write.customize") == 1


def test_another_app_keeps_its_identity_after_one_lacked_the_scope():
    with patch("connectors.slack.WebClient", autospec=True) as client:
        post = client.return_value.chat_postMessage
        post.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}, {"ts": "2.1"}]

        slack.post_message("C1", "one", token="xoxb-1", username="Anna")
        slack.post_message("C1", "two", token="xoxb-2", username="Anna")

    assert post.call_args_list[2].kwargs["username"] == "Anna"


def test_the_missing_scope_is_kept_in_redis_under_a_hash_of_the_token_with_an_expiry(redis):
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}]

        slack.post_message("C1", "one", token="xoxb-secret-token", username="Anna")

    [(key, ttl)] = redis.marks.items()
    assert key.startswith("slack_no_customize:") and "xoxb-secret-token" not in key
    assert ttl == slack._NO_CUSTOMIZE_TTL_SECONDS


def test_the_mark_is_found_by_a_later_job_that_shares_nothing_but_redis(redis):
    with patch("connectors.slack.WebClient", autospec=True) as client:
        post = client.return_value.chat_postMessage
        post.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}]
        slack.post_message("C1", "one", token="xoxb-1", username="Anna")

        post.side_effect = [{"ts": "2.1"}]
        post.reset_mock()
        slack.post_message("C1", "two", token="xoxb-1", username="Anna")  # another job: same Redis, fresh process

    post.assert_called_once_with(channel="C1", text="two", thread_ts=None, blocks=None)


def test_a_post_without_an_identity_never_touches_redis(redis):
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.return_value = {"ts": "1.1"}

        slack.post_message("C1", "plain", token="xoxb-1")

    redis.exists.assert_not_called()


def test_an_unreadable_redis_means_asking_for_the_identity_again(redis):
    redis.exists.side_effect = RedisError("down")
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.return_value = {"ts": "1.1"}

        slack.post_message("C1", "one", token="xoxb-1", username="Anna")

    assert client.return_value.chat_postMessage.call_args.kwargs["username"] == "Anna"


def test_a_redis_that_cannot_store_the_mark_does_not_stop_the_message(redis):
    redis.set.side_effect = RedisError("down")
    with patch("connectors.slack.WebClient", autospec=True) as client:
        client.return_value.chat_postMessage.side_effect = [slack_error("missing_scope"), {"ts": "1.1"}]

        assert slack.post_message("C1", "one", token="xoxb-1", username="Anna") == "1.1"


def test_the_app_is_not_marked_when_the_retry_as_the_bot_fails_too(redis):
    with patch("connectors.slack.WebClient", autospec=True) as client, pytest.raises(SlackApiError):
        client.return_value.chat_postMessage.side_effect = [slack_error("missing_scope"), slack_error("channel_not_found")]

        slack.post_message("C1", "one", token="xoxb-1", username="Anna")

    assert redis.marks == {}


# --- channel analysis reads


def channels_page(names, next_cursor=""):
    return {"channels": [{"id": f"C{n}", "name": n} for n in names], "response_metadata": {"next_cursor": next_cursor}}


def test_find_channel_id_follows_the_cursor_until_the_name_is_found():
    client = MagicMock()
    client.conversations_list.side_effect = [channels_page(["a", "b"], "c2"), channels_page(["mail", "z"])]

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        found = slack.find_channel_id("mail", token="t")

    assert found == "Cmail"
    assert [c.kwargs["cursor"] for c in client.conversations_list.call_args_list] == [None, "c2"]
    assert client.conversations_list.call_args.kwargs["exclude_archived"] is True
    assert client.conversations_list.call_args.kwargs["types"] == "public_channel,private_channel"


def test_find_channel_id_returns_none_after_the_last_page_and_after_the_page_cap():
    client = MagicMock()
    client.conversations_list.return_value = channels_page(["a"])
    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.find_channel_id("nope", token="t") is None

    client.conversations_list.reset_mock()
    client.conversations_list.return_value = channels_page(["a"], "more")
    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.find_channel_id("nope", token="t", max_pages=3) is None
    assert client.conversations_list.call_count == 3


def test_get_channel_info_returns_the_channel_dict():
    client = MagicMock()
    client.conversations_info.return_value = {"channel": {"id": "C1", "is_private": True}}

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.get_channel_info("C1", token="t") == {"id": "C1", "is_private": True}


def test_is_channel_member_pages_through_the_members_and_gives_up_at_the_cap():
    client = MagicMock()
    client.conversations_members.side_effect = [
        {"members": ["U1"], "response_metadata": {"next_cursor": "c2"}},
        {"members": ["U2"], "response_metadata": {"next_cursor": ""}},
    ]
    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.is_channel_member("C1", "U2", token="t", max_pages=5) is True

    client.conversations_members.side_effect = None
    client.conversations_members.return_value = {"members": ["U1"], "response_metadata": {"next_cursor": "more"}}
    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.is_channel_member("C1", "UX", token="t", max_pages=2) is None

    client.conversations_members.return_value = {"members": ["U1"], "response_metadata": {"next_cursor": ""}}
    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.is_channel_member("C1", "UX", token="t", max_pages=2) is False


def test_workspace_url_file_info_and_file_listing_pass_the_answer_through():
    client = MagicMock()
    client.auth_test.return_value = {"url": "https://acme.slack.com/"}
    client.files_info.return_value = {"file": {"id": "F1"}}
    client.files_list.return_value = {"files": [{"id": "F1"}]}

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        assert slack.get_workspace_url("t") == "https://acme.slack.com/"
        assert slack.get_file_info("F1", token="t") == {"id": "F1"}
        assert slack.list_files("C1", token="t") == [{"id": "F1"}]

    client.files_list.assert_called_once_with(channel="C1", count=1)


class FakeStream:
    def __init__(self, status=200, content_type="text/csv", chunks=(b"id\n", b"1\n")):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        return iter(self._chunks)


URL = "https://files.slack.com/files-pri/T-F1/download/rows.csv"


def download(stream, url=URL, max_bytes=100):
    with patch("connectors.slack.httpx.stream", autospec=True, return_value=stream) as fake:
        return slack.download_file(url, token="xoxb-t", max_bytes=max_bytes), fake


def test_download_file_sends_the_token_and_returns_the_bytes():
    data, fake = download(FakeStream())

    assert data == b"id\n1\n"
    assert fake.call_args.kwargs["headers"] == {"Authorization": "Bearer xoxb-t"}
    assert fake.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("url", ["https://evil.test/f.csv", "http://files.slack.com/f.csv", "https://slack.com.evil.test/f.csv", "https://notslack.com/f.csv"])
def test_download_file_refuses_a_url_that_is_not_slacks_and_never_sends_the_token(url):
    with patch("connectors.slack.httpx.stream", autospec=True) as fake, pytest.raises(slack.FileDownloadError, match="not hosted on Slack"):
        slack.download_file(url, token="xoxb-t", max_bytes=100)

    fake.assert_not_called()


@pytest.mark.parametrize(
    "stream, message",
    [
        (FakeStream(status=403), "403"),
        (FakeStream(content_type="text/html; charset=utf-8"), "files:read"),
        (FakeStream(chunks=(b"x" * 60, b"x" * 60)), "larger than"),
    ],
    ids=["status", "html-login-page", "too-big"],
)
def test_download_file_explains_why_it_gave_up(stream, message):
    with pytest.raises(slack.FileDownloadError, match=message):
        download(stream)


def test_conversations_replies_stops_at_max_messages_with_a_page_no_bigger_than_that():
    client = MagicMock()
    client.conversations_replies.return_value = page([{"ts": str(n)} for n in range(5)], "more")

    with patch("connectors.slack.WebClient", autospec=True, return_value=client):
        messages = slack.conversations_replies("C1", "1.0", token="t", max_messages=3)

    assert [m["ts"] for m in messages] == ["0", "1", "2"]
    assert client.conversations_replies.call_count == 1
    assert client.conversations_replies.call_args.kwargs["limit"] == 3
