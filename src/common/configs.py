"""Common configuration values shared across receiver, worker, and scheduler."""

import os
from dataclasses import asdict, dataclass, field
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
    # The model that turns chunks of Slack messages into records for analyze_channel; the cheap router model unless set.
    EXTRACTION_MODEL: str = os.getenv("EXTRACTION_MODEL", ROUTER_MODEL)

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
    category: Literal["core", "schedule", "data_access", "secrets", "pr_reminder", "dataset_sync", "characters", "channel_analysis"] = "core"

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


def _window_properties(**descriptions: str) -> dict:
    """The tool parameters that pick a channel window and a page of it, shared by read_channel_messages and analyze_channel.

    `descriptions` replaces the description of a parameter for the tool that needs its own wording (keyed by parameter name).
    """
    properties = {
        "lookback_days": {"type": "integer", "description": "Window length in days. Default 30. lookback_days + end_days_ago <= 30."},
        "end_days_ago": {
            "type": "integer",
            "description": "Where the window ends, in days before now. Default 0. To compare periods: last week = lookback_days 7, end_days_ago 7.",
        },
        "contains": {"type": "array", "items": {"type": "string"}, "description": "Up to 5 case-insensitive terms; only messages containing any of them count."},
        "include_replies": {"type": "boolean", "description": "Also read thread replies. Default true."},
        "before": {
            "type": "string",
            "description": "Resume cursor: the `before` value from the previous result, copied exactly (it also fixes the window). Omit on the first call.",
        },
    }
    for name, description in descriptions.items():
        properties[name] = {**properties[name], "description": description}
    return properties


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
    PR_REMINDER_MAX_MENTIONS: int = 10
    PR_REMINDER_MIN_INTERVAL_MINUTES: int = 30
    PR_REMINDER_MAX_EXPIRY_HOURS: int = 8760
    # The emoji the bot leaves on a Slack message whose PRs are all approved or merged, or all closed without merging, and
    # how many it adds in one run: a first run over a long backlog would otherwise burst past Slack's reactions.add rate
    # limit; the next ticks finish the rest.
    PR_REMINDER_APPROVED_REACTION: str = "white_check_mark"
    PR_REMINDER_CLOSED_REACTION: str = "x"
    PR_REMINDER_MAX_REACTIONS_PER_RUN: int = 25
    # A one-off check inside a thread that links several PRs asks which one to remind instead of reminding all of them;
    # this many are listed in that question.
    PR_REMINDER_MAX_CHOICES_LISTED: int = 10
    # The zone a reminder's cron is written in when the request names none. When reminders may fire is part of the cron
    # itself (e.g. `*/30 8-17 * * 1-5`), written by the agent following the pr-reminders skill.
    PR_REMINDER_DEFAULT_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    # How long a thread keeps the character picked for it (characters/infra/redis_thread_characters.py). Long enough
    # for a PR thread to be reminded for as long as a reminder can look back (PR_REMINDER_MAX_LOOKBACK_HOURS).
    THREAD_CHARACTER_TTL_SECONDS: int = 14 * 24 * 3600

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
                "Check whether GitHub pull requests shared in Slack are approved, and remind the given people (by @mention) "
                "in the Slack thread of each PR that is still waiting for review. Use when someone asks to check PR "
                "approval status or to remind reviewers about PRs. If the request itself contains PR links, pass them in "
                "pr_urls: then only those PRs are checked, and the rest of the thread or channel is not looked at. Without "
                "pr_urls, scope 'thread' looks at the PR links in the current thread: with one PR it reminds for it, and with several "
                "it posts nothing and lists them so you can ask the user which one(s) to remind, or whether to remind all (then call "
                "again with pr_urls for the chosen ones, or with remind_all true). Scope 'channel' "
                "checks PR links in the channel's top-level messages (not thread replies) over the last lookback_hours. "
                "Needs the people to remind: take them from the @mentions in the user's message, and if "
                "there are none, ask the user who to remind instead of calling this."
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
                    "pr_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "GitHub PR links typed in the request itself. When given, only these PRs are checked.",
                    },
                    "remind_all": {
                        "type": "boolean",
                        "description": (
                            "Scope thread only: true when the user said to remind every PR in the thread (or answered 'all' when asked which). "
                            "Without it, a thread that links several PRs is not reminded; the tool lists them instead."
                        ),
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Slack user IDs of the people to remind, from the <@U123> mentions in the user's message (not the bot's own).",
                    },
                },
                "required": ["scope"],
            },
            category="pr_reminder",
        ),
        AgentTool(
            name="schedule_pr_reminder",
            description=(
                "Set up a recurring PR approval reminder: on every cron tick the bot re-checks the PRs shared in this "
                "thread or channel, reminds the given people (by @mention) in the thread of each PR still waiting for "
                "review. It keeps running until an admin removes it, even when nothing is waiting, since a PR can be shared later; "
                "set expires_in_hours only when the user asks for a time limit. Channel scope reads top-level messages only. "
                "Needs the people to remind: take them from the @mentions in the user's message, and if there are none, "
                "ask the user who to remind instead of calling this. Reminders go out whenever the cron fires, so the cron "
                "must carry the hours and days they are allowed (see its description). "
                "Use this, not add_schedule, for recurring PR review reminders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cron": {
                        "type": "string",
                        "description": (
                            f"Cron expression (5 fields) in `timezone`, at most every {PR_REMINDER_MIN_INTERVAL_MINUTES} minutes. It also sets when "
                            "reminders may go out. Unless the user names other hours or days, limit it to weekdays 08:00-17:59 so nobody is pinged "
                            "at night or at the weekend: every 30 minutes is '*/30 8-17 * * 1-5', hourly is '0 8-17 * * 1-5'. "
                            "Use '*' for the day of week only when the user asks for weekends or every day."
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            f"IANA timezone the cron is written in, e.g. 'Asia/Ho_Chi_Minh' or 'UTC'. Default {PR_REMINDER_DEFAULT_TIMEZONE}. "
                            "Pass it only when the user names another timezone; write the cron in that zone's local time, not converted."
                        ),
                    },
                    "scope": {"type": "string", "enum": ["thread", "channel"], "description": "Where to look for PR links on every run."},
                    "lookback_hours": {"type": "integer", "description": "Channel scope only. Default 72, max 336."},
                    "channel": {"type": "string", "description": "Channel scope only: Slack channel ID to watch. Defaults to the current channel."},
                    "expires_in_hours": {
                        "type": "integer",
                        "description": (
                            "Only when the user explicitly asks the reminder to stop after a time (e.g. 'for 72h', 'for 3 days' is 72): "
                            f"hours until it ends, max {PR_REMINDER_MAX_EXPIRY_HOURS} (one year). Omit otherwise: the reminder then runs until removed. "
                            "Do not set it from a lookback window ('PRs in the last 3d')."
                        ),
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Slack user IDs of the people to remind, from the <@U123> mentions in the user's message (not the bot's own).",
                    },
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
            name="list_synced_datasets",
            description=(
                "List the reference datasets synced from Databricks into the bot's database (name, what it is for, "
                "when it was last synced, whether the last sync worked, row count, and any recurring sync schedule). "
                "Admin-only. Use it before syncing or scheduling when the user hasn't named a dataset, or asks what "
                "can be synced or when something was last synced."
            ),
            parameters={"type": "object", "properties": {}},
            category="dataset_sync",
        ),
        AgentTool(
            name="sync_dataset",
            description=(
                "Run a sync of one dataset now: re-read it from Databricks and replace the stored copy. Admin-only. "
                "Use when an admin asks to sync, refresh or update a dataset (e.g. store locations, row-filter tags). "
                "A sync that would shrink the dataset sharply is refused; pass force=true only after the admin has "
                "seen that refusal and explicitly confirmed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset name exactly as list_synced_datasets shows it, e.g. 'store_locations'."},
                    "force": {"type": "boolean", "description": "Replace the stored copy even if the new one is much smaller. Only after explicit admin confirmation."},
                },
                "required": ["dataset"],
            },
            category="dataset_sync",
        ),
        AgentTool(
            name="schedule_dataset_sync",
            description=(
                "Schedule a recurring sync of one dataset. Admin-only. Runs silently; a failed run is reported in the "
                "channel. Only one recurring sync per dataset can exist: if one does, its id is returned instead. "
                "Cron is UTC and has a minimum interval, stated in the tool result if it is too frequent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset name exactly as list_synced_datasets shows it."},
                    "cron": {"type": "string", "description": "5-field cron expression in UTC, e.g. '0 2 * * *' for daily at 02:00 UTC."},
                    "channel": {"type": "string", "description": "Slack channel ID that failed runs are reported in. Defaults to the channel of the request."},
                },
                "required": ["dataset", "cron"],
            },
            category="dataset_sync",
        ),
        AgentTool(
            name="check_channel_access",
            description=(
                "Check whether the bot and the requester may read a Slack channel, before reading it. Admin-only. Call it first, "
                "before read_channel_messages / read_channel_file and before scheduling a recurring channel analysis. It reads no "
                "messages. It returns OK, or every problem found (not a member, missing Slack permission, private channel the requester "
                "is not in, archived, not found) with how to fix each; when a problem is blocking, stop and tell the admin."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID (C…), a channel link like <#C123|name>, or the channel name."},
                },
                "required": ["channel"],
            },
            category="channel_analysis",
        ),
        AgentTool(
            name="read_channel_messages",
            description=(
                "Read the messages of a Slack channel for a time window, newest first, in pages, to analyze them (count, compare, "
                "summarize, find what is missing). Admin-only; run check_channel_access first. Bot reports are flattened to text; "
                "files are listed with their id (open one with read_channel_file). The result says how many messages the page holds "
                "and whether the window is finished; if it is not, call again with `before` from the result and the same other "
                "arguments until it is, or say that you covered only part of the window. Reaches at most 30 days back."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID (C…), a channel link like <#C123|name>, or the channel name."},
                    **_window_properties(
                        contains="Up to 5 case-insensitive terms; a message is kept if any appears in its text, author, file names or replies.",
                        include_replies="Also read thread replies. Default true; false is faster when threads don't matter.",
                    ),
                },
                "required": ["channel"],
            },
            category="channel_analysis",
        ),
        AgentTool(
            name="read_channel_file",
            description=(
                "Read the text of a file attached to a message in a Slack channel (CSV, TSV, text, JSON, PDF with text, Excel .xlsx), "
                "in pages. Admin-only. Use it only when the question needs what is inside a file; the file id (F…) is shown next to the "
                "file in read_channel_messages. The result says where the next page starts; pass it as `offset`."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "The channel the file was posted in (same forms as read_channel_messages)."},
                    "file_id": {"type": "string", "description": "File ID, e.g. F0123ABCD, as shown in the message."},
                    "offset": {"type": "integer", "description": "Character offset to start at, from the previous result. Default 0."},
                },
                "required": ["channel", "file_id"],
            },
            category="channel_analysis",
        ),
        AgentTool(
            name="analyze_channel",
            description=(
                "Count and compare across the messages (and optionally the files) of a Slack channel, for exact numbers over many "
                "messages: a cheaper model turns them into records you describe, and code counts the records. Admin-only; run "
                "check_channel_access first. Use it for counts, rankings, 'which ones', what stopped appearing, and anything too big "
                "for read_channel_messages; use read_channel_messages for timelines, quotes and wording. You never see the raw "
                "messages, only counts, a sample and links as evidence. Counts are for one call: if the result says more messages "
                "remain, call again with `before` and the same other arguments and add the counts up. Mention failed chunks and "
                "files not read in your answer. Reaches at most 30 days back."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID (C…), a channel link, or the channel name."},
                    "instruction": {
                        "type": "string",
                        "description": "How to fill one record, e.g. 'one record per table row in each report: its name and whether it was skipped, failed or loaded'.",
                    },
                    "fields": {
                        "type": "array",
                        "description": "The record shape, 1 to 8 fields.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "snake_case name, e.g. 'table'."},
                                "description": {"type": "string", "description": "What to put in it."},
                                "allowed_values": {"type": "array", "items": {"type": "string"}, "description": "If given, only these values are accepted."},
                            },
                            "required": ["name", "description"],
                        },
                    },
                    "group_by": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 field names to count by; omit for one overall total."},
                    **_window_properties(
                        contains="Up to 5 terms; only messages containing any of them are analyzed.",
                        include_replies="Also analyze thread replies. Default true.",
                    ),
                    "include_files": {"type": "boolean", "description": "Also extract from attached files (CSV, text, PDF, Excel). Default false."},
                },
                "required": ["channel", "instruction", "fields"],
            },
            category="channel_analysis",
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
        AgentTool(
            name="list_characters",
            description=(
                "List the characters (voices) the bot can speak in: name, song and artist, whether it is enabled, what it is bound to "
                "(features and skills), whether its row is valid, and when it was last changed. Admin-only. Use when an admin asks "
                "which characters, personas or voices exist, or why one is not being used."
            ),
            parameters={"type": "object", "properties": {}},
            category="characters",
        ),
        AgentTool(
            name="add_character",
            description=(
                "Add a new character (voice) to the characters table. Admin-only. It only adds: a name that already exists is "
                "refused and nothing is changed. Name, display_name and at least one binding are required. First collect every "
                "required value from the admin, asking for what is missing. Then call with dry_run true to validate it, fix any problem "
                "with the admin, show the full definition (including the Vietnamese text), and only after a clear yes call again with "
                "dry_run false. Voice text may use only the placeholders listed for each field and must not contain < or >, "
                "@channel, @here or @everyone."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Lower-case slug, letters, digits, - or _, up to 64 characters, e.g. 'em-gai-mua'."},
                    "display_name": {
                        "type": "string",
                        "description": "Sender name shown on Slack, up to 80 characters. Defaults to name. Use the song title, not an artist's name.",
                    },
                    "song": {"type": "string", "description": "The song this character follows, if any."},
                    "artist": {"type": "string", "description": "The song's artist, for credit only."},
                    "icon_emoji": {"type": "string", "description": "Standard Slack emoji shown as the avatar, e.g. ':cloud_with_rain:'."},
                    "icon_url": {"type": "string", "description": "https image URL shown as the avatar. An icon_emoji wins when both are given."},
                    "bindings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "What the character speaks for: 'feature:pr_reminder' (PR reminder messages, needs voices.pr_reminder) and "
                            "'skill:<skill name>' (agent replies for that skill, needs persona), e.g. 'skill:metadata'. "
                            "'skill:core' makes it the default voice of every task that has no character of its own."
                        ),
                    },
                    "persona": {"type": "string", "description": "Instructions for the agent's tone, up to 1500 characters. Required by any skill binding."},
                    "voices": {
                        "type": "object",
                        "description": (
                            "One object per feature. For pr_reminder: headers (list of 1-10 templates, each with {who} and {subject} or {Subject}), and "
                            "optional subject_one, subject_many (needs {count}), flair_fresh, flair_stale, flair_ancient, author_court_with_waiting, "
                            "author_court_alone, author_line, schedule_footer, expired (these two need {schedule_id}), unreadable ({schedule_id} and "
                            "{error_code}). Each template is at most 300 characters. A field left out uses the default wording."
                        ),
                    },
                    "enabled": {"type": "boolean", "description": "Default true. Pass false to store it without using it yet."},
                    "dry_run": {"type": "boolean", "description": "true checks the request and stores nothing. Always check with true before storing."},
                },
                "required": ["name", "display_name", "bindings"],
            },
            category="characters",
        ),
        AgentTool(
            name="update_character",
            description=(
                "Change a stored character, including what it speaks for (its bindings to features and skills). Admin-only. Only the "
                "fields given are changed; the character is then checked as a whole and nothing is saved if a rule fails. The name "
                "cannot be changed. First call with dry_run true to validate the change, fix any problem with the admin, tell the "
                "admin exactly what will change, and only after a clear yes call again with dry_run false. To retire a character "
                "without deleting it, pass enabled false."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the stored character, as list_characters shows it."},
                    "display_name": {"type": "string", "description": "New sender name shown on Slack."},
                    "song": {"type": "string"},
                    "artist": {"type": "string"},
                    "icon_emoji": {"type": "string", "description": "New avatar emoji, e.g. ':star:'. An empty text clears it."},
                    "icon_url": {"type": "string", "description": "New avatar https URL. An empty text clears it."},
                    "persona": {"type": "string", "description": "New persona text, up to 1500 characters. Replaces the old one."},
                    "enabled": {"type": "boolean", "description": "false stops the bot using it, true brings it back."},
                    "bindings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Replace all bindings with exactly these ('feature:pr_reminder', 'skill:<name>', 'skill:core').",
                    },
                    "add_bindings": {"type": "array", "items": {"type": "string"}, "description": "Bindings to add to the current ones, e.g. ['skill:metadata']."},
                    "remove_bindings": {"type": "array", "items": {"type": "string"}, "description": "Bindings to remove from the current ones."},
                    "dry_run": {"type": "boolean", "description": "true checks the change and stores nothing. Always check with true before storing."},
                    "voices": {
                        "type": "object",
                        "description": (
                            "Per feature, the full new voice block (e.g. {'pr_reminder': {'headers': [...]}}). It replaces that "
                            "feature's whole block, and other features' blocks are left as they are."
                        ),
                    },
                },
                "required": ["name"],
            },
            category="characters",
        ),
        AgentTool(
            name="remove_character",
            description=(
                "Delete a stored character. Admin-only. Threads that used it get a new character next time. A character that ships "
                "as a seed file comes back on the next deploy, and the result says so. Before calling, name the character and wait "
                "for a clear yes."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Name of the stored character, as list_characters shows it."}},
                "required": ["name"],
            },
            category="characters",
        ),
    )


@dataclass(frozen=True)
class ReminderMessages:
    """The parts of a PR reminder's wording that no character can change (`agent/pr_reminders/message.py`).

    Vietnamese. The rest of the wording (headers, quips, the author-court lines, the footer and the closing notes) is the
    voice of a reminder: `DEFAULT_VOICE` in message.py holds the default one and a character can replace any of it.
    `{placeholders}` are filled in by message.py.
    """

    # The header rotates every HEADER_ROTATION_SECONDS so a recurring reminder doesn't repeat itself word for word.
    HEADER_ROTATION_SECONDS: int = 30 * 60

    # How long a PR has waited, e.g. "20 phút", and the line it appears in.
    AGE_MINUTES: str = "{n} phút"
    AGE_HOURS: str = "{n} giờ"
    AGE_DAYS: str = "{n} ngày"
    AGE_UNKNOWN: str = "?"
    WAITING_LINE: str = "chờ {age}{flair}"

    # When the quip after the age applies: fresh PRs sparkle, forgotten ones gather dust. Nothing in between. The quips
    # themselves are part of the voice.
    FLAIR_FRESH_UNDER_HOURS: int = 1
    FLAIR_STALE_AFTER_DAYS: int = 3
    FLAIR_ANCIENT_AFTER_DAYS: int = 7


@dataclass(frozen=True)
class ReminderConfig:
    """The settings of a recurring PR reminder, stored in `schedules.config`.

    `mentions` are the Slack user IDs reminded on every run. `origin_channel` / `origin_thread_ts` are the thread the
    reminder was requested in: it gets the schedule id and the final note, and a `thread`-scope reminder rescans it on
    every run. Reminders themselves go to each PR's own thread.

    `expires_at` is the unix time the reminder ends, set only when the requester asked for a time limit; without it the
    reminder runs until it is removed. `last_reminded` is progress the reminder job saves as it runs: it maps
    "channel:thread_ts" to the unix time of the last reminder there, so that when more threads are waiting than fit in
    one run, the ones reminded longest ago go first.
    """

    scope: str
    origin_channel: str
    origin_thread_ts: str
    mentions: tuple[str, ...]
    lookback_hours: int | None = None
    created_by: str | None = None
    expires_at: float | None = None
    last_reminded: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self) | {"mentions": list(self.mentions)}

    @classmethod
    def from_dict(cls, data: dict) -> "ReminderConfig":
        """Rebuild a config read back from the database, which doesn't constrain `config`.

        Raises:
            ValueError: a key is missing, or the scope or lookback is invalid.
        """
        lookback_hours = data.get("lookback_hours")
        if lookback_hours is not None and (isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int)):
            raise ValueError(f"PR reminder config has a non-integer lookback_hours: {lookback_hours!r}")
        mentions = data.get("mentions")
        if not isinstance(mentions, list) or not mentions or not all(isinstance(mention, str) for mention in mentions):
            raise ValueError(f"PR reminder config needs a non-empty list of mentions: {mentions!r}")
        expires_at = data.get("expires_at")
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, int | float)):
            raise ValueError(f"PR reminder config has a non-numeric expires_at: {expires_at!r}")
        last_reminded = data.get("last_reminded", {})
        if not isinstance(last_reminded, dict) or not all(isinstance(at, int | float) and not isinstance(at, bool) for at in last_reminded.values()):
            raise ValueError(f"PR reminder config has an invalid last_reminded: {last_reminded!r}")
        try:
            config = cls(
                scope=data["scope"],
                origin_channel=data["origin_channel"],
                origin_thread_ts=data["origin_thread_ts"],
                mentions=tuple(mentions),
                lookback_hours=lookback_hours,
                created_by=data.get("created_by"),
                expires_at=expires_at,
                last_reminded=last_reminded,
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
