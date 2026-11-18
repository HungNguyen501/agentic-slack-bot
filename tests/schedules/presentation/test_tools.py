from unittest.mock import create_autospec

import pytest

from schedules.core.ports import ScheduleRepository
from schedules.presentation import tools


@pytest.fixture
def repo():
    return create_autospec(ScheduleRepository, instance=True)


def test_list_schedules_only_asks_for_the_calling_bots_rows(repo):
    repo.get_schedules.return_value = []

    result = tools.dispatch_schedule_tool("list_schedules", {}, "bot-a", repo)

    repo.get_schedules.assert_called_once_with("bot-a", active_only=True)
    assert result == "No schedules configured."


def test_update_schedule_passes_the_calling_bot(repo):
    repo.update_schedule.return_value = None

    result = tools.dispatch_schedule_tool("update_schedule", {"id": "s1", "cron": "0 9 * * *"}, "bot-a", repo)

    repo.update_schedule.assert_called_once_with("s1", cron="0 9 * * *", channel=None, question=None, bot_id="bot-a")
    assert result == "Schedule `s1` not found or no fields to update."


def test_remove_schedule_passes_the_calling_bot_and_reports_not_found(repo):
    repo.remove_schedule.return_value = False

    result = tools.dispatch_schedule_tool("remove_schedule", {"id": "s1"}, "bot-a", repo)

    repo.remove_schedule.assert_called_once_with("s1", bot_id="bot-a")
    assert result == "Schedule `s1` not found."
