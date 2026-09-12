"""Common configuration values shared across receiver, worker, and scheduler."""

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Configs:
    """Common config vars and env vars.

    Read once at import time and used as class-level constants (e.g. `Configs.REDIS_URL`) —
    no instantiation required. Per-bot values (tokens, signing secrets, admin users) live in
    the Supabase `bots` table, not here.
    """

    SUPABASE_DB_URL: str = os.environ["SUPABASE_DB_URL"]
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY", None)
    ROUTER_MODEL: str = os.getenv("ROUTER_MODEL", "gpt-4o-mini")

    DATABRICKS_HOST: str | None = os.getenv("DATABRICKS_HOST", None)
    DATABRICKS_WAREHOUSE_ID: str | None = os.getenv("DATABRICKS_WAREHOUSE_ID", None)
    DATABRICKS_ACCESS_TOKEN: str | None = os.getenv("DATABRICKS_ACCESS_TOKEN", None)

    GIT_REPO_PAT_DATA_PLATFORM: str | None = os.getenv("GIT_REPO_PAT_DATA_PLATFORM", None)

    WORKER_COUNT: int = int(os.getenv("WORKER_COUNT", "2"))
    SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "180"))

    NGROK_AUTHTOKEN: str | None = os.getenv("NGROK_AUTHTOKEN", None)


@dataclass(frozen=True)
class GithubConfigs:
    """GitHub repo constants for the data-platform access-control PR flow (`connectors/github.py`)."""

    OWNER: str = "VireoAI"
    REPO: str = "vireox-data-platform"
    BASE_BRANCH: str = "main"
    API: str = f"https://api.github.com/repos/{OWNER}/{REPO}"


@dataclass(frozen=True)
class AgentTool:
    """One OpenAI function-calling tool definition for the agent loop (`worker/agent.py`)."""

    name: str
    description: str
    parameters: dict
    category: Literal["core", "schedule", "data_access"] = "core"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class AgentConfigs:
    """Agent loop constants (`worker/agent.py`)."""

    GPT_MODEL: str = "gpt-5.5-2026-04-23"

    # How long the "Open Form" button posted by _dispatch_data_access_tool stays clickable.
    # worker/agent.py stamps this into the button's value as expires_at; receiver/app.py just
    # compares expires_at against the current time, no TTL math of its own.
    ACCESS_REQUEST_BUTTON_TTL_SECONDS: int = 3600

    TOOLS: tuple[AgentTool, ...] = (
        AgentTool(
            name="execute_query",
            description=(
                "Execute a SQL SELECT query against Databricks system tables. "
                "Use this to answer questions about catalogs, schemas, tables, columns, "
                "jobs, job run history, or data lineage."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A SQL SELECT statement targeting Databricks system tables."},
                },
                "required": ["sql"],
            },
        ),
        AgentTool(
            name="get_job_run_details",
            description=(
                "Fetch the actual error message and per-task failure details for a specific "
                "Databricks job run via the Jobs REST API. Use this after identifying a failed "
                "run_id from execute_query to get the human-readable error text for investigation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "The job run ID from job_run_timeline.run_id"},
                },
                "required": ["run_id"],
            },
        ),
        AgentTool(
            name="list_schedules",
            description="List all active scheduled questions.",
            parameters={"type": "object", "properties": {}},
            category="schedule",
        ),
        AgentTool(
            name="add_schedule",
            description="Create a new scheduled question posted to a Slack channel on a cron.",
            parameters={
                "type": "object",
                "properties": {
                    "cron": {"type": "string", "description": "Cron expression (5 fields, UTC). E.g. '0 9 * * 1-5'"},
                    "channel": {"type": "string", "description": "Slack channel ID, e.g. C1234567890"},
                    "question": {"type": "string", "description": "The question text to send on schedule"},
                },
                "required": ["cron", "channel", "question"],
            },
            category="schedule",
        ),
        AgentTool(
            name="update_schedule",
            description="Update one or more fields of an existing schedule by its UUID.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Schedule UUID"},
                    "cron": {"type": "string"},
                    "channel": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["id"],
            },
            category="schedule",
        ),
        AgentTool(
            name="remove_schedule",
            description="Permanently delete a scheduled question by its UUID.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Schedule UUID to remove"},
                },
                "required": ["id"],
            },
            category="schedule",
        ),
        AgentTool(
            name="request_data_access",
            description=(
                "Start a data access request for the current user. Use when the user asks to "
                "request, get, or apply for access to a dataset, table filter, or scoped permission. "
                "This checks eligibility and, if allowed, posts an interactive form for the user to fill in."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "request_type": {
                        "type": "string",
                        "description": "The access request category, e.g. 'table_row_filter_access'.",
                    },
                },
                "required": ["request_type"],
            },
            category="data_access",
        ),
    )
