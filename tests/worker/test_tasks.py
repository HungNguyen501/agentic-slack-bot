from unittest.mock import patch

from connectors.db.bots import BotConfig
from worker import tasks

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def test_process_pr_reminder_runs_the_reminder_for_the_loaded_bot():
    with patch("worker.tasks.get_bot", autospec=True, return_value=BOT) as get_bot, patch("worker.tasks.run_reminder", autospec=True) as run_reminder:
        tasks.process_pr_reminder("s1", "bot-a")

    get_bot.assert_called_once_with("bot-a")
    run_reminder.assert_called_once_with(BOT, "s1")
