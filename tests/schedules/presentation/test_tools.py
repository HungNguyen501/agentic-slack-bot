from unittest.mock import create_autospec

import pytest

from schedules.core.models import Schedule
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

    result = tools.dispatch_schedule_tool("update_schedule", {"id": "s1", "cron": "0 9 * * *"}, "bot-a", repo, "UADMIN")

    repo.update_schedule.assert_called_once_with("s1", cron="0 9 * * *", channel=None, question=None, bot_id="bot-a", run_as="UADMIN")
    assert result == "Schedule `s1` not found or no fields to update."


def test_list_schedules_shows_whose_access_each_one_runs_with_and_a_dash_for_a_legacy_one(repo):
    repo.get_schedules.return_value = [
        Schedule(id="s1", cron="0 9 * * 1", channel="C1", question="weekly", run_as="UADMIN"),
        Schedule(id="s2", cron="0 8 * * *", channel="C2", question="old one"),
    ]

    result = tools.dispatch_schedule_tool("list_schedules", {}, "bot-a", repo)

    header, _, first, second = result.strip("`\n").split("\n")
    assert header.split()[:5] == ["ID", "Cron", "Timezone", "Channel", "Owner"]
    column = header.index("Owner")
    end = column + len("UADMIN")
    assert first[column:end] == "UADMIN"
    assert second[column] == "-" and second[column + 1] == " "
    assert first.endswith("weekly") and second.endswith("old one")


def test_add_schedule_records_the_admin_it_will_run_with(repo):
    repo.add_schedule.return_value = Schedule(id="s9", cron="0 9 * * 1", channel="C1", question="q")

    result = tools.dispatch_schedule_tool("add_schedule", {"cron": "0 9 * * 1", "channel": "C1", "question": "q"}, "bot-a", repo, "UADMIN")

    repo.add_schedule.assert_called_once_with("0 9 * * 1", "C1", "q", bot_id="bot-a", run_as="UADMIN")
    assert result == "Schedule created — id: `s9`"


def test_remove_schedule_passes_the_calling_bot_and_reports_not_found(repo):
    repo.remove_schedule.return_value = False

    result = tools.dispatch_schedule_tool("remove_schedule", {"id": "s1"}, "bot-a", repo)

    repo.remove_schedule.assert_called_once_with("s1", bot_id="bot-a")
    assert result == "Schedule `s1` not found."
