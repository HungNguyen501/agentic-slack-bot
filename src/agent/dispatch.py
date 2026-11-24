"""Tool schema exposure and per-call routing for the agent loop."""
import logging
from dataclasses import dataclass

from agent.access_control.tools import dispatch_data_access_tool
from agent.pr_reminders.tools import dispatch_pr_reminder_tool
from agent.service_principal_secrets.tools import dispatch_secret_generation_tool
from bots.core.models import BotConfig
from channel_analysis.core.service import ChannelAnalysisService
from channel_analysis.presentation.tools import dispatch_channel_analysis_tool
from characters.core.service import CharacterService
from characters.presentation.tools import dispatch_character_tool
from common.configs import AgentConfigs
from connectors import databricks
from dataset_sync.core.service import DatasetSyncService
from dataset_sync.presentation.tools import dispatch_dataset_sync_tool
from schedules.core.ports import ScheduleRepository
from schedules.presentation.tools import dispatch_schedule_tool

log = logging.getLogger("agent.dispatch")

# Tools that return what channels and files hold; text in them is not the admin's, so once one has run, the rest of that run
# may use only the read-only tools below (a crafted post must not be able to remove a schedule or add a character).
UNTRUSTED_CONTENT_TOOLS = frozenset({"read_channel_messages", "read_channel_file", "analyze_channel"})
# `execute_query` is not here on purpose: connectors/databricks/sql.py only checks that a statement starts with SELECT or WITH,
# so it can't be relied on to stop a statement a post asked for. It comes back to this set only once it is read-only for sure.
READ_ONLY_TOOLS = frozenset({"get_job_run_details", "list_schedules", "list_synced_datasets", "list_characters", "check_channel_access"} | UNTRUSTED_CONTENT_TOOLS)


@dataclass(frozen=True)
class UnattendedRun:
    """A run with no requester (a scheduled question): the admin-only tool categories it may use, and whose access it runs with."""

    categories: frozenset[str]
    run_as: str | None


@dataclass(frozen=True)
class ToolServices:
    """The migrated features' use cases and stores the tool handlers run on, built by the worker's composition root."""

    schedules: ScheduleRepository
    dataset_sync: DatasetSyncService
    characters: CharacterService
    channel_analysis: ChannelAnalysisService


def get_agent_tools(read_only: bool = False) -> list[dict]:
    """Return the OpenAI function-calling tool schema for all agent tools, or only the read-only ones after untrusted content was read."""
    return [tool.to_openai_schema() for tool in AgentConfigs.TOOLS if not read_only or tool.name in READ_ONLY_TOOLS]


def tool_category(name: str) -> str | None:
    """Look up the AgentTool.category for a tool by name, or None if it's not a known tool."""
    return next((t.category for t in AgentConfigs.TOOLS if t.name == name), None)


def dispatch_tool(
    name: str,
    args: dict,
    user_id: str | None,
    admin_users: frozenset[str],
    bot: BotConfig,
    channel: str,
    thread_ts: str,
    services: ToolServices,
    unattended: UnattendedRun | None = None,
) -> str:
    """Route a single tool call to the correct handler, enforcing the per-bot admin whitelist.

    Args:
        name: Tool name as returned by the LLM.
        args: Parsed JSON arguments from the LLM tool call.
        user_id: Slack user ID of the requester.
        admin_users: Per-bot set of Slack user IDs allowed to manage schedules.
        bot: Per-bot config carrying bot_id, bot_token, admin_users, enabled_skills.
        channel: Slack channel ID the request was made in.
        thread_ts: Thread timestamp of the conversation.
        services: Use cases and stores for the tools that run on them.
        unattended: Set for a run with no requester (`user_id` None, a scheduled question): the only admin-only categories it may
            use, and the admin (`run_as`) whose access they run with. Every other admin-only category stays refused for it.

    Returns:
        String result to pass back to the LLM as the tool response.
    """
    if name == "execute_query":
        sql = args.get("sql", "")
        log.info("Databricks query: %s", sql)
        return databricks.run_query(sql)

    if name == "get_job_run_details":
        run_id = args.get("run_id", "")
        log.info("Fetching job run details for run_id=%s", run_id)
        return databricks.get_job_run(run_id)

    if tool_category(name) == "schedule":
        if user_id not in admin_users:
            return "You are not authorized to manage schedules."
        if name == "schedule_pr_reminder":
            return dispatch_pr_reminder_tool(name, args, user_id, channel, thread_ts, bot, services.characters)
        return dispatch_schedule_tool(name, args, bot.bot_id, services.schedules, user_id)

    if tool_category(name) == "dataset_sync":
        if user_id not in admin_users:
            return "You are not authorized to manage dataset syncs."
        return dispatch_dataset_sync_tool(name, args, user_id, channel, bot, services.dataset_sync)

    if tool_category(name) == "characters":
        if user_id not in admin_users:
            return "You are not authorized to manage characters."
        return dispatch_character_tool(name, args, services.characters)

    if tool_category(name) == "channel_analysis":
        requester = user_id
        if requester is None and unattended and "channel_analysis" in unattended.categories:
            if unattended.run_as is None:
                return "This scheduled question has no recorded owner, so it can't read channels. Ask an admin to schedule it again."
            requester = unattended.run_as
        if requester not in admin_users:
            return "You are not authorized to read channels for analysis."
        return dispatch_channel_analysis_tool(name, args, requester, bot, services.channel_analysis)

    if tool_category(name) == "pr_reminder":
        return dispatch_pr_reminder_tool(name, args, user_id, channel, thread_ts, bot, services.characters)

    if tool_category(name) == "data_access":
        return dispatch_data_access_tool(args, user_id, channel, thread_ts, bot)

    if tool_category(name) == "secrets":
        return dispatch_secret_generation_tool(args, user_id, channel, bot)

    return f"Unknown tool: {name}"
