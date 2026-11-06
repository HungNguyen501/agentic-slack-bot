from unittest.mock import patch

import pytest

from agent import dispatch
from connectors.db.bots import BotConfig

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")


def call(name, user_id, admin_users=frozenset({"UADMIN"})):
    return dispatch.dispatch_tool(name, {"scope": "channel"}, user_id, admin_users, BOT, "C1", "1.0")


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
    handler.assert_called_once_with("schedule_pr_reminder", {"scope": "channel"}, "UADMIN", "C1", "1.0", BOT)
    schedule_handler.assert_not_called()


@pytest.mark.parametrize("user_id", ["UADMIN", "UOTHER"])
def test_check_pr_approvals_is_open_to_everyone(user_id):
    with patch("agent.dispatch.dispatch_pr_reminder_tool", autospec=True, return_value="ok") as handler:
        reply = call("check_pr_approvals", user_id)

    assert reply == "ok"
    handler.assert_called_once_with("check_pr_approvals", {"scope": "channel"}, user_id, "C1", "1.0", BOT)


def test_the_other_schedule_tools_still_use_the_schedule_handler():
    with patch("agent.dispatch.dispatch_schedule_tool", autospec=True, return_value="ok") as handler:
        reply = dispatch.dispatch_tool("remove_schedule", {"id": "s1"}, "UADMIN", frozenset({"UADMIN"}), BOT, "C1", "1.0")

    assert reply == "ok"
    handler.assert_called_once_with("remove_schedule", {"id": "s1"}, "bot-a")


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
    handler.assert_called_once_with("sync_dataset", {"scope": "channel"}, "UADMIN", "C1", BOT)
