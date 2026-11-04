from unittest.mock import patch

from agent import schedules


def test_list_schedules_only_asks_for_the_calling_bots_rows():
    with patch("agent.schedules.get_schedules", autospec=True, return_value=[]) as get:
        result = schedules.dispatch_schedule_tool("list_schedules", {}, "bot-a")

    get.assert_called_once_with("bot-a", active_only=True)
    assert result == "No schedules configured."


def test_update_schedule_passes_the_calling_bot():
    with patch("agent.schedules.update_schedule", autospec=True, return_value=None) as update:
        result = schedules.dispatch_schedule_tool("update_schedule", {"id": "s1", "cron": "0 9 * * *"}, "bot-a")

    update.assert_called_once_with("s1", cron="0 9 * * *", channel=None, question=None, bot_id="bot-a")
    assert result == "Schedule `s1` not found or no fields to update."


def test_remove_schedule_passes_the_calling_bot_and_reports_not_found():
    with patch("agent.schedules.remove_schedule", autospec=True, return_value=False) as remove:
        result = schedules.dispatch_schedule_tool("remove_schedule", {"id": "s1"}, "bot-a")

    remove.assert_called_once_with("s1", bot_id="bot-a")
    assert result == "Schedule `s1` not found."
