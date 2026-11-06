"""Tool schema exposure and per-call routing for the agent loop."""
import logging

from agent.access_control.tools import dispatch_data_access_tool
from agent.dataset_sync.tools import dispatch_dataset_sync_tool
from agent.pr_reminders.tools import dispatch_pr_reminder_tool
from agent.schedules import dispatch_schedule_tool
from agent.service_principal_secrets.tools import dispatch_secret_generation_tool
from common.configs import AgentConfigs
from connectors import databricks
from connectors.db.bots import BotConfig

log = logging.getLogger("agent.dispatch")


def get_agent_tools() -> list[dict]:
    """Return the OpenAI function-calling tool schema for all agent tools."""
    return [tool.to_openai_schema() for tool in AgentConfigs.TOOLS]


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
            return dispatch_pr_reminder_tool(name, args, user_id, channel, thread_ts, bot)
        return dispatch_schedule_tool(name, args, bot.bot_id)

    if tool_category(name) == "dataset_sync":
        if user_id not in admin_users:
            return "You are not authorized to manage dataset syncs."
        return dispatch_dataset_sync_tool(name, args, user_id, channel, bot)

    if tool_category(name) == "pr_reminder":
        return dispatch_pr_reminder_tool(name, args, user_id, channel, thread_ts, bot)

    if tool_category(name) == "data_access":
        return dispatch_data_access_tool(args, user_id, channel, thread_ts, bot)

    if tool_category(name) == "secrets":
        return dispatch_secret_generation_tool(args, user_id, channel, bot)

    return f"Unknown tool: {name}"
