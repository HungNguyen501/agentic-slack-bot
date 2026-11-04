"""Common configuration values shared across receiver, worker, and scheduler."""

import os
from dataclasses import asdict, dataclass
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
    ROUTER_MODEL: str = os.getenv("ROUTER_MODEL", "gpt-6.1-sol")

    DATABRICKS_HOST: str | None = os.getenv("DATABRICKS_HOST", None)
    DATABRICKS_WAREHOUSE_ID: str | None = os.getenv("DATABRICKS_WAREHOUSE_ID", None)
    DATABRICKS_ACCESS_TOKEN: str | None = os.getenv("DATABRICKS_ACCESS_TOKEN", None)

    GIT_REPO_PAT_DATA_PLATFORM: str | None = os.getenv("GIT_REPO_PAT_DATA_PLATFORM", None)
    # GitHub App auth (preferred over the PAT when GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are both set).
    # The private key is the PEM contents; literal "\n" sequences are accepted so it fits on one env line.
    GITHUB_APP_ID: str | None = os.getenv("GITHUB_APP_ID", None)
    GITHUB_APP_PRIVATE_KEY: str | None = os.getenv("GITHUB_APP_PRIVATE_KEY", None)
    GITHUB_APP_INSTALLATION_ID: str | None = os.getenv("GITHUB_APP_INSTALLATION_ID", None)

    # Fernet key (Fernet.generate_key()) used to encrypt cached service-principal secrets
    # at rest in Supabase — see common/crypto.py.
    SECRET_ENCRYPTION_KEY: str | None = os.getenv("SECRET_ENCRYPTION_KEY", None)
    # Public base URL of the receiver (e.g. the ngrok/prod domain) used to build the
    # one-time secret-view link posted to Slack — see receiver/app.py's /secrets/{token}.
    SECRET_LINK_BASE_URL: str | None = os.getenv("SECRET_LINK_BASE_URL", None)

    WORKER_COUNT: int = int(os.getenv("WORKER_COUNT", "2"))
    SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "180"))

    NGROK_AUTHTOKEN: str | None = os.getenv("NGROK_AUTHTOKEN", None)


@dataclass(frozen=True)
class GithubConfigs:
    """GitHub repo constants for the data-platform access-control PR flow (`connectors/github.py`)."""

    OWNER: str = "100xteam-ai"
    REPO: str = "vireox-data-platform"
    BASE_BRANCH: str = "main"
    API: str = f"https://api.github.com/repos/{OWNER}/{REPO}"
    # Not tied to the repo above: PR review reminders query pull requests in whichever repos the credentials can read.
    GRAPHQL_API: str = "https://api.github.com/graphql"


@dataclass(frozen=True)
class AgentTool:
    """One OpenAI function-calling tool definition for the agent loop (`agent/agent.py`)."""

    name: str
    description: str
    parameters: dict
    category: Literal["core", "schedule", "data_access", "secrets", "pr_reminder"] = "core"

    def to_openai_schema(self) -> dict:
        # Responses API tools are flat (no nested "function" key) and default to strict=True,
        # which would reject these schemas (optional properties, no additionalProperties: false).
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": False,
        }


@dataclass(frozen=True)
class AgentConfigs:
    """Agent loop constants (`agent/agent.py`)."""

    GPT_MODEL: str = "gpt-6.1-sol"
    REASONING_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "medium"

    # How long the "Open Form" button posted by dispatch_data_access_tool stays clickable.
    # agent/access_control/tools.py stamps this into the button's value as expires_at; receiver/app.py just
    # compares expires_at against the current time, no TTL math of its own.
    ACCESS_REQUEST_BUTTON_TTL_SECONDS: int = 3600

    # How long a one-time service-principal-secret view link stays valid in Redis before
    # it's treated as expired (see agent/service_principal_secrets/tools.py's secret_link:{token} key).
    SECRET_LINK_TTL_SECONDS: int = 600

    # PR review reminders (agent/pr_reminders/).
    PR_REMINDER_SCOPES: tuple[str, ...] = ("thread", "channel")
    PR_REMINDER_DEFAULT_LOOKBACK_HOURS: int = 72
    PR_REMINDER_MAX_LOOKBACK_HOURS: int = 336
    PR_REMINDER_MAX_MESSAGES_SCANNED: int = 1000
    # Checking more PRs than are posted about keeps the oldest waiting ones from being crowded out by newer approved ones.
    PR_REMINDER_MAX_PRS_CHECKED: int = 100
    PR_REMINDER_MAX_THREADS_POSTED: int = 25
    PR_REMINDER_MAX_ACTIVE_PER_CHANNEL: int = 5
    PR_REMINDER_MIN_INTERVAL_MINUTES: int = 30
    PR_REMINDER_LIFETIME_DAYS: int = 7
    # Recurring reminders post from START (inclusive) to END (exclusive), local hour in Vietnam. Vietnam has no DST, so a
    # fixed UTC offset is enough and needs no tz database in the image.
    PR_REMINDER_ACTIVE_START_HOUR: int = 7
    PR_REMINDER_ACTIVE_END_HOUR: int = 22
    PR_REMINDER_UTC_OFFSET_HOURS: int = 7

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
            name="check_pr_approvals",
            description=(
                "Check whether GitHub pull requests shared in Slack are approved, and remind the channel (@channel) "
                "in the Slack thread of each PR that is still waiting for review. Use when someone asks to check PR "
                "approval status or to remind reviewers about PRs. scope 'thread' checks the PR links in the current "
                "thread (or in this message); scope 'channel' checks PR links shared in this channel over the last "
                "lookback_hours. People named in the request are not pinged; the reminder always pings @channel."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["thread", "channel"], "description": "Where to look for PR links."},
                    "lookback_hours": {
                        "type": "integer",
                        "description": (
                            f"Channel scope only: how far back to look. Default {PR_REMINDER_DEFAULT_LOOKBACK_HOURS}, "
                            f"max {PR_REMINDER_MAX_LOOKBACK_HOURS}. '3d' is 72."
                        ),
                    },
                    "pr_urls": {"type": "array", "items": {"type": "string"}, "description": "GitHub PR links typed in the request itself."},
                },
                "required": ["scope"],
            },
            category="pr_reminder",
        ),
        AgentTool(
            name="schedule_pr_reminder",
            description=(
                "Set up a recurring PR approval reminder: on every cron tick the bot re-checks the PRs shared in this "
                "thread or channel, reminds @channel in the thread of each PR still waiting for review, and stops by "
                "itself once no PR is waiting. Reminders are only sent between "
                f"{PR_REMINDER_ACTIVE_START_HOUR:02d}:00 and {PR_REMINDER_ACTIVE_END_HOUR:02d}:00 Vietnam time (UTC+{PR_REMINDER_UTC_OFFSET_HOURS}). "
                "Use this, not add_schedule, for recurring PR review reminders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cron": {
                        "type": "string",
                        "description": f"Cron expression (5 fields, UTC), at most every {PR_REMINDER_MIN_INTERVAL_MINUTES} minutes. E.g. '*/30 * * * *'.",
                    },
                    "scope": {"type": "string", "enum": ["thread", "channel"], "description": "Where to look for PR links on every run."},
                    "lookback_hours": {"type": "integer", "description": "Channel scope only. Default 72, max 336."},
                    "channel": {"type": "string", "description": "Channel scope only: Slack channel ID to watch. Defaults to the current channel."},
                },
                "required": ["cron", "scope"],
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
        AgentTool(
            name="generate_service_principal_secret",
            description=(
                "Generate (or return a still-valid cached) Databricks service-principal OAuth "
                "client secret. Only callable by users listed as reviewers for this request type "
                "in this channel. Use when a reviewer asks to generate, rotate, or get a secret "
                "for a service principal / service account."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service_account": {
                        "type": "string",
                        "description": "Email address identifying the service principal, e.g. 'braiden.haas@wholesome.co'.",
                    },
                },
                "required": ["service_account"],
            },
            category="secrets",
        ),
    )


@dataclass(frozen=True)
class ReminderConfig:
    """The settings of a recurring PR reminder, stored in `schedules.config`.

    `origin_channel` / `origin_thread_ts` are the thread the reminder was requested in: it gets the schedule id and the
    final note, and a `thread`-scope reminder rescans it on every run. Reminders themselves go to each PR's own thread.
    """

    scope: str
    origin_channel: str
    origin_thread_ts: str
    lookback_hours: int | None = None
    created_by: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ReminderConfig":
        """Rebuild a config read back from the database, which doesn't constrain `config`.

        Raises:
            ValueError: a key is missing, or the scope or lookback is invalid.
        """
        lookback_hours = data.get("lookback_hours")
        if lookback_hours is not None and (isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int)):
            raise ValueError(f"PR reminder config has a non-integer lookback_hours: {lookback_hours!r}")
        try:
            config = cls(
                scope=data["scope"],
                origin_channel=data["origin_channel"],
                origin_thread_ts=data["origin_thread_ts"],
                lookback_hours=lookback_hours,
                created_by=data.get("created_by"),
            )
        except KeyError as exc:
            raise ValueError(f"PR reminder config is missing {exc}") from exc
        if config.scope not in AgentConfigs.PR_REMINDER_SCOPES:
            raise ValueError(f"PR reminder config has an unknown scope: {config.scope!r}")
        if config.scope == "channel" and not config.lookback_hours:
            raise ValueError("A channel-scope PR reminder needs lookback_hours")
        return config

    def label(self) -> str:
        """Description stored in the schedule's `question` column, so list_schedules stays readable."""
        if self.scope == "thread":
            return "PR review reminder · thread scope"
        return f"PR review reminder · channel scope, last {self.lookback_hours}h"
