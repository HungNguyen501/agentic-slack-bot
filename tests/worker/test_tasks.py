from unittest.mock import patch

import pytest
from rq.timeouts import JobTimeoutException
from slack_sdk.errors import SlackApiError, SlackRequestError

from agent.agent import AgentReply
from agent.dispatch import UnattendedRun
from bots.core.models import BotConfig
from characters.core.models import Character
from worker import tasks

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def test_process_pr_reminder_runs_the_reminder_for_the_loaded_bot():
    with patch("worker.tasks.wiring", autospec=True) as wiring, patch("worker.tasks.run_reminder", autospec=True) as run_reminder:
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        tasks.process_pr_reminder("s1", "bot-a")

    wiring.bot_repository.return_value.get_by_id.assert_called_once_with("bot-a")
    run_reminder.assert_called_once_with(BOT, "s1", characters=wiring.character_service.return_value)


@pytest.fixture(autouse=True)
def fresh_bot_user_id_cache():
    tasks._bot_user_id.cache_clear()
    yield
    tasks._bot_user_id.cache_clear()


@pytest.fixture
def bot_user_id():
    with patch("worker.tasks.slack.get_bot_user_id", autospec=True, return_value="UBOT") as lookup:
        yield lookup


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<@UBOT> remind <@U1> and <@U2> about this PR", "remind <@U1> and <@U2> about this PR"),
        ("hey <@UBOT> remind <@U1>", "hey remind <@U1>"),
        ("<@UBOT|bot-name> remind <@U1>", "remind <@U1>"),
        ("<@UBOT>  <@UBOT> what failed?", "what failed?"),
        ("what failed?", "what failed?"),
        ("<@UBOT>", ""),
        ("<@UBOTX> is someone else", "<@UBOTX> is someone else"),
    ],
    ids=["leading", "middle", "with-name", "twice", "no-mention", "only-mention", "similar-id"],
)
def test_strip_bot_mention_keeps_other_peoples_mentions(bot_user_id, text, expected):
    assert tasks._strip_bot_mention(text, "xoxb-1") == expected


def test_strip_bot_mention_looks_the_bot_up_once_per_token(bot_user_id):
    tasks._strip_bot_mention("<@UBOT> a", "xoxb-1")
    tasks._strip_bot_mention("<@UBOT> b", "xoxb-1")

    bot_user_id.assert_called_once_with("xoxb-1")


@pytest.mark.parametrize(
    "error",
    [SlackApiError("nope", {"error": "ratelimited"}), SlackRequestError("bad request"), TimeoutError("timed out"), ConnectionResetError("reset")],
    ids=["api-error", "request-error", "timeout", "connection-reset"],
)
def test_strip_bot_mention_falls_back_to_stripping_every_mention_when_the_lookup_fails(error):
    with patch("worker.tasks.slack.get_bot_user_id", autospec=True, side_effect=error):
        assert tasks._strip_bot_mention("<@UBOT> remind <@U1>", "xoxb-1") == "remind"


def test_reply_to_mention_gives_the_agent_the_other_people_the_request_names():
    with (
        patch("worker.tasks.wiring", autospec=True) as wiring,
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks._animate_thinking", autospec=True),
        patch("worker.tasks.run_agent", autospec=True, return_value=AgentReply("done")) as run_agent,
    ):
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        slack.get_bot_user_id.return_value = "UBOT"
        slack.split_message.side_effect = lambda text: [text]
        tasks.reply_to_mention("C1", "1.0", "bot-a", user="U9", text="<@UBOT> remind <@U1> <@U2> about this PR")

    assert run_agent.call_args.args[0] == "remind <@U1> <@U2> about this PR"


def test_process_dataset_sync_runs_the_service_for_the_loaded_bot():
    with patch("worker.tasks.wiring", autospec=True) as wiring:
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        tasks.process_dataset_sync("s1", "bot-a")

    wiring.dataset_sync_service.return_value.run_scheduled_sync.assert_called_once_with(BOT, "s1")


ANNA = Character(name="anna", display_name="Anna", icon_emoji=":star:", bindings=("skill:core",), persona="Speak like Anna.")


@pytest.fixture
def mention():
    """Run reply_to_mention with the agent answering `reply`; yields a function that returns the mocked slack module."""

    def run(reply=None, error=None):
        with (
            patch("worker.tasks.wiring", autospec=True) as wiring,
            patch("worker.tasks.slack", autospec=True) as slack,
            patch("worker.tasks._animate_thinking", autospec=True),
            patch("worker.tasks.run_agent", autospec=True, return_value=reply, side_effect=error),
        ):
            wiring.bot_repository.return_value.get_by_id.return_value = BOT
            slack.get_bot_user_id.return_value = "UBOT"
            slack.split_message.side_effect = lambda text: text.split("|")
            slack.post_message.side_effect = ["placeholder-ts", "reply-ts-1", "reply-ts-2"]
            ts = tasks.reply_to_mention("C1", "1.0", "bot-a", user="U9", text="<@UBOT> which tables?")
        return slack, ts

    return run


def test_a_reply_without_a_character_replaces_the_placeholder_in_place(mention):
    slack, ts = mention(AgentReply("one|two"))

    slack.update_message.assert_called_once_with("C1", "placeholder-ts", "one", token="xoxb-test")
    slack.post_message.assert_called_with("C1", "two", "1.0", token="xoxb-test")
    slack.delete_message.assert_not_called()
    assert ts == "reply-ts-1"


def test_a_reply_with_a_character_is_posted_under_its_name_and_icon_and_the_placeholder_removed(mention):
    slack, ts = mention(AgentReply("one|two", ANNA))

    posted = slack.post_message.call_args_list[1:]
    assert [call.args[1] for call in posted] == ["one", "two"]
    assert all(call.args[2] == "1.0" and call.kwargs == {"token": "xoxb-test", "username": "Anna", "icon_emoji": ":star:"} for call in posted)
    slack.update_message.assert_not_called()
    slack.delete_message.assert_called_once_with("C1", "placeholder-ts", token="xoxb-test")
    assert ts == "reply-ts-2"


def test_the_placeholder_is_deleted_after_the_answer_is_posted(mention):
    slack, _ = mention(AgentReply("one", ANNA))

    names = [name for name, *_ in slack.mock_calls if name in {"post_message", "delete_message"}]
    assert names == ["post_message", "post_message", "delete_message"]


@pytest.mark.parametrize(
    "error",
    [SlackApiError("boom", {"error": "message_not_found"}), SlackRequestError("timeout"), ConnectionResetError("reset")],
    ids=["slack-api-error", "slack-client-error", "os-error"],
)
def test_a_placeholder_that_cannot_be_deleted_does_not_fail_the_reply(error):
    with patch("worker.tasks.slack", autospec=True) as slack:
        slack.delete_message.side_effect = error

        tasks._delete_placeholder("C1", "1.5", "xoxb-test")

    slack.delete_message.assert_called_once_with("C1", "1.5", token="xoxb-test")


def test_a_reply_is_posted_once_even_when_the_placeholder_cannot_be_deleted(mention):
    with patch("worker.tasks.slack.delete_message", autospec=True, side_effect=SlackRequestError("timeout")):
        slack, ts = mention(AgentReply("one", ANNA))

    assert ts == "reply-ts-1"
    assert [call.args[1] for call in slack.post_message.call_args_list[1:]] == ["one"]


def test_an_agent_that_raises_gets_the_plain_apology_in_place(mention):
    slack, _ = mention(error=RuntimeError("boom"))

    assert "Sorry, I ran into an error" in slack.update_message.call_args.args[2]
    slack.delete_message.assert_not_called()


def test_an_empty_reply_removes_the_placeholder_whatever_the_character(mention):
    slack, ts = mention(AgentReply("", ANNA))

    slack.delete_message.assert_called_once_with("C1", "placeholder-ts", token="xoxb-test")
    slack.update_message.assert_not_called()
    assert ts == "1.0"


@pytest.fixture
def scheduled():
    def run(reply=None, error=None):
        with (
            patch("worker.tasks.wiring", autospec=True) as wiring,
            patch("worker.tasks.slack", autospec=True) as slack,
            patch("worker.tasks.run_agent", autospec=True, return_value=reply, side_effect=error),
            patch("worker.tasks.summarize_answer", autospec=True, return_value="the summary") as summarize,
        ):
            wiring.bot_repository.return_value.get_by_id.return_value = BOT
            slack.split_message.side_effect = lambda text: [text]
            slack.post_message.side_effect = ["header-ts", "answer-ts"]
            ts = tasks.process_scheduled_question("C1", "how many jobs failed?", "bot-a")
        return slack, summarize, ts

    return run


def test_a_scheduled_answer_is_posted_under_the_character_and_summarized_in_its_voice(scheduled):
    slack, summarize, ts = scheduled(AgentReply("full answer", ANNA))

    summarize.assert_called_once_with("how many jobs failed?", "full answer", ANNA)
    assert slack.post_message.call_args_list[1].args[:3] == ("C1", "full answer", "header-ts")
    assert slack.post_message.call_args_list[1].kwargs == {"token": "xoxb-test", "username": "Anna", "icon_emoji": ":star:"}
    assert ts == "answer-ts"


def test_a_scheduled_answer_without_a_character_is_posted_as_the_bot(scheduled):
    slack, summarize, _ = scheduled(AgentReply("full answer"))

    summarize.assert_called_once_with("how many jobs failed?", "full answer", None)
    assert slack.post_message.call_args_list[1].kwargs == {"token": "xoxb-test"}


def test_a_scheduled_question_that_fails_apologizes_as_the_bot(scheduled):
    slack, _, _ = scheduled(error=RuntimeError("boom"))

    assert "Sorry, I ran into an error" in slack.post_message.call_args_list[1].args[1]
    assert slack.post_message.call_args_list[1].kwargs == {"token": "xoxb-test"}


def test_a_scheduled_question_may_use_the_channel_analysis_tools_as_the_admin_it_runs_for():
    with (
        patch("worker.tasks.wiring", autospec=True) as wiring,
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks.run_agent", autospec=True, return_value=AgentReply("answer")) as run_agent,
        patch("worker.tasks.summarize_answer", autospec=True, return_value="summary"),
    ):
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        slack.split_message.side_effect = lambda text: [text]
        slack.post_message.return_value = "ts"
        tasks.process_scheduled_question("C1", "analyze the reports channel", "bot-a", run_as="UADMIN")

    assert run_agent.call_args.kwargs["unattended"] == UnattendedRun(frozenset({"channel_analysis"}), "UADMIN")
    assert "user_id" not in run_agent.call_args.kwargs


def test_a_scheduled_question_of_a_schedule_with_no_owner_runs_with_no_one_to_read_channels_as():
    with (
        patch("worker.tasks.wiring", autospec=True) as wiring,
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks.run_agent", autospec=True, return_value=AgentReply("answer")) as run_agent,
        patch("worker.tasks.summarize_answer", autospec=True, return_value="summary"),
    ):
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        slack.split_message.side_effect = lambda text: [text]
        slack.post_message.return_value = "ts"
        tasks.process_scheduled_question("C1", "what failed?", "bot-a")

    assert run_agent.call_args.kwargs["unattended"].run_as is None


def test_a_mention_never_gets_unattended_categories():
    with (
        patch("worker.tasks.wiring", autospec=True) as wiring,
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks._animate_thinking", autospec=True),
        patch("worker.tasks.run_agent", autospec=True, return_value=AgentReply("done")) as run_agent,
    ):
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        slack.get_bot_user_id.return_value = "UBOT"
        slack.split_message.side_effect = lambda text: [text]
        tasks.reply_to_mention("C1", "1.0", "bot-a", user="U9", text="<@UBOT> hi")

    assert "unattended" not in run_agent.call_args.kwargs


def test_a_job_timeout_in_the_agent_is_one_apology_and_not_a_failed_job_that_rq_would_retry():
    with (
        patch("worker.tasks.wiring", autospec=True) as wiring,
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks._animate_thinking", autospec=True),
        patch("worker.tasks.run_agent", autospec=True, side_effect=JobTimeoutException("Task exceeded maximum timeout value (600 seconds)")),
    ):
        wiring.bot_repository.return_value.get_by_id.return_value = BOT
        slack.get_bot_user_id.return_value = "UBOT"
        slack.split_message.side_effect = lambda text: [text]
        slack.post_message.return_value = "thinking-ts"
        tasks.reply_to_mention("C1", "1.0", "bot-a", user="U9", text="<@UBOT> analyze the reports channel")

    assert "Sorry, I ran into an error" in slack.update_message.call_args.args[2]
