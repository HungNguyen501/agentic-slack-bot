from unittest.mock import patch

import pytest
from slack_sdk.errors import SlackApiError

from connectors.db.bots import BotConfig
from worker import tasks

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def test_process_pr_reminder_runs_the_reminder_for_the_loaded_bot():
    with patch("worker.tasks.get_bot", autospec=True, return_value=BOT) as get_bot, patch("worker.tasks.run_reminder", autospec=True) as run_reminder:
        tasks.process_pr_reminder("s1", "bot-a")

    get_bot.assert_called_once_with("bot-a")
    run_reminder.assert_called_once_with(BOT, "s1")


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


def test_strip_bot_mention_falls_back_to_stripping_every_mention_when_the_lookup_fails():
    with patch("worker.tasks.slack.get_bot_user_id", autospec=True, side_effect=SlackApiError("nope", {"error": "ratelimited"})):
        assert tasks._strip_bot_mention("<@UBOT> remind <@U1>", "xoxb-1") == "remind"


def test_reply_to_mention_gives_the_agent_the_other_people_the_request_names():
    with (
        patch("worker.tasks.get_bot", autospec=True, return_value=BOT),
        patch("worker.tasks.slack", autospec=True) as slack,
        patch("worker.tasks._animate_thinking", autospec=True),
        patch("worker.tasks.run_agent", autospec=True, return_value="done") as run_agent,
    ):
        slack.get_bot_user_id.return_value = "UBOT"
        slack.split_message.side_effect = lambda text: [text]
        tasks.reply_to_mention("C1", "1.0", "bot-a", user="U9", text="<@UBOT> remind <@U1> <@U2> about this PR")

    assert run_agent.call_args.args[0] == "remind <@U1> <@U2> about this PR"
