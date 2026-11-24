from unittest.mock import create_autospec, patch

import pytest

from agent import dispatch
from bots.core.models import BotConfig
from channel_analysis.core.service import ChannelAnalysisService
from characters.core.service import CharacterService
from dataset_sync.core.service import DatasetSyncService
from schedules.core.ports import ScheduleRepository

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")
SERVICES = dispatch.ToolServices(
    schedules=create_autospec(ScheduleRepository),
    dataset_sync=create_autospec(DatasetSyncService),
    characters=create_autospec(CharacterService),
    channel_analysis=create_autospec(ChannelAnalysisService),
)


def call(name, user_id, admin_users=frozenset({"UADMIN"})):
    return dispatch.dispatch_tool(name, {"scope": "channel"}, user_id, admin_users, BOT, "C1", "1.0", SERVICES)


def test_schedule_pr_reminder_is_admin_only():
    with patch("agent.dispatch.dispatch_pr_reminder_tool", autospec=True) as handler:
        reply = call("schedule_pr_reminder", "UOTHER")

    assert reply == "You are not authorized to manage schedules."
    handler.assert_not_called()


def test_schedule_pr_reminder_goes_to_the_pr_reminder_handler_for_admins():
    with (
        patch("agent.dispatch.dispatch_pr_reminder_tool", autospec=True, return_value="ok") as handler,
        patch("agent.dispatch.dispatch_schedule_tool", autospec=True) as schedule_handler,
    ):
        reply = call("schedule_pr_reminder", "UADMIN")

    assert reply == "ok"
    handler.assert_called_once_with("schedule_pr_reminder", {"scope": "channel"}, "UADMIN", "C1", "1.0", BOT, SERVICES.characters)
    schedule_handler.assert_not_called()


@pytest.mark.parametrize("user_id", ["UADMIN", "UOTHER"])
def test_check_pr_approvals_is_open_to_everyone(user_id):
    with patch("agent.dispatch.dispatch_pr_reminder_tool", autospec=True, return_value="ok") as handler:
        reply = call("check_pr_approvals", user_id)

    assert reply == "ok"
    handler.assert_called_once_with("check_pr_approvals", {"scope": "channel"}, user_id, "C1", "1.0", BOT, SERVICES.characters)


def test_the_other_schedule_tools_still_use_the_schedule_handler():
    with patch("agent.dispatch.dispatch_schedule_tool", autospec=True, return_value="ok") as handler:
        reply = dispatch.dispatch_tool("remove_schedule", {"id": "s1"}, "UADMIN", frozenset({"UADMIN"}), BOT, "C1", "1.0", SERVICES)

    assert reply == "ok"
    handler.assert_called_once_with("remove_schedule", {"id": "s1"}, "bot-a", SERVICES.schedules)


@pytest.mark.parametrize("name", ["list_synced_datasets", "sync_dataset", "schedule_dataset_sync"])
def test_dataset_sync_tools_are_admin_only(name):
    with patch("agent.dispatch.dispatch_dataset_sync_tool", autospec=True) as handler:
        reply = call(name, "UOTHER")

    assert reply == "You are not authorized to manage dataset syncs."
    handler.assert_not_called()


def test_dataset_sync_tools_go_to_their_handler_for_admins():
    with patch("agent.dispatch.dispatch_dataset_sync_tool", autospec=True, return_value="ok") as handler:
        reply = call("sync_dataset", "UADMIN")

    assert reply == "ok"
    handler.assert_called_once_with("sync_dataset", {"scope": "channel"}, "UADMIN", "C1", BOT, SERVICES.dataset_sync)


CHARACTER_TOOLS = ["list_characters", "add_character", "update_character", "remove_character"]


@pytest.mark.parametrize("name", CHARACTER_TOOLS)
def test_character_tools_are_admin_only(name):
    with patch("agent.dispatch.dispatch_character_tool", autospec=True) as handler:
        reply = call(name, "UOTHER")

    assert reply == "You are not authorized to manage characters."
    handler.assert_not_called()


@pytest.mark.parametrize("name", CHARACTER_TOOLS)
def test_character_tools_go_to_the_character_handler_for_admins(name):
    with patch("agent.dispatch.dispatch_character_tool", autospec=True, return_value="ok") as handler:
        reply = call(name, "UADMIN")

    assert reply == "ok"
    handler.assert_called_once_with(name, {"scope": "channel"}, SERVICES.characters)


def test_every_character_tool_is_a_known_tool_of_the_characters_category():
    assert [dispatch.tool_category(name) for name in CHARACTER_TOOLS] == ["characters"] * 4


def test_the_files_are_no_longer_synced_by_a_tool():
    assert dispatch.tool_category("sync_characters") is None


CHANNEL_TOOLS = ["check_channel_access", "read_channel_messages", "read_channel_file"]


@pytest.mark.parametrize("name", CHANNEL_TOOLS)
def test_channel_tools_are_a_known_category(name):
    assert dispatch.tool_category(name) == "channel_analysis"


@pytest.mark.parametrize("name", CHANNEL_TOOLS)
def test_channel_tools_are_admin_only(name):
    with patch("agent.dispatch.dispatch_channel_analysis_tool", autospec=True) as handler:
        reply = call(name, "UOTHER")

    assert reply == "You are not authorized to read channels for analysis."
    handler.assert_not_called()


@pytest.mark.parametrize("name", CHANNEL_TOOLS)
def test_channel_tools_go_to_their_handler_for_admins_with_the_requester(name):
    with patch("agent.dispatch.dispatch_channel_analysis_tool", autospec=True, return_value="ok") as handler:
        reply = call(name, "UADMIN")

    assert reply == "ok"
    handler.assert_called_once_with(name, {"scope": "channel"}, "UADMIN", BOT, SERVICES.channel_analysis)


def test_a_run_with_no_requester_is_refused_unless_it_is_allowed_the_category():
    with patch("agent.dispatch.dispatch_channel_analysis_tool", autospec=True, return_value="ok") as handler:
        refused = call("read_channel_messages", None)
        allowed = dispatch.dispatch_tool(
            "read_channel_messages", {}, None, frozenset({"UADMIN"}), BOT, "C1", "1.0", SERVICES, unattended_categories=frozenset({"channel_analysis"})
        )

    assert refused == "You are not authorized to read channels for analysis."
    assert allowed == "ok"
    handler.assert_called_once_with("read_channel_messages", {}, None, BOT, SERVICES.channel_analysis)


def test_allowing_a_run_without_a_requester_the_channel_tools_does_not_open_the_other_admin_tools():
    with patch("agent.dispatch.dispatch_schedule_tool", autospec=True) as handler:
        reply = dispatch.dispatch_tool(
            "remove_schedule", {"id": "s1"}, None, frozenset({"UADMIN"}), BOT, "C1", "1.0", SERVICES, unattended_categories=frozenset({"channel_analysis"})
        )

    assert reply == "You are not authorized to manage schedules."
    handler.assert_not_called()


def test_a_requester_who_is_not_an_admin_gains_nothing_from_the_unattended_categories():
    reply = dispatch.dispatch_tool(
        "read_channel_messages", {}, "UOTHER", frozenset({"UADMIN"}), BOT, "C1", "1.0", SERVICES, unattended_categories=frozenset({"channel_analysis"})
    )

    assert reply == "You are not authorized to read channels for analysis."


def test_after_channel_content_was_read_only_read_only_tools_are_offered():
    every = {tool["name"] for tool in dispatch.get_agent_tools()}
    read_only = {tool["name"] for tool in dispatch.get_agent_tools(read_only=True)}

    writes = {"remove_schedule", "add_character", "schedule_pr_reminder", "check_pr_approvals", "request_data_access", "generate_service_principal_secret"}
    assert writes <= every - read_only
    assert read_only == dispatch.READ_ONLY_TOOLS
    assert every >= dispatch.READ_ONLY_TOOLS
