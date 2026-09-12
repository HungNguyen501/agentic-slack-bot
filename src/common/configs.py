"""Common configuration values shared across receiver, worker, and scheduler."""

import os
from dataclasses import dataclass


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
class AgentConfigs:
    """Agent loop constants (`worker/agent.py`)."""

    GPT_MODEL: str = "gpt-5.5-2026-04-23"

    # How long the "Open Form" button posted by _dispatch_data_access_tool stays clickable.
    # worker/agent.py stamps this into the button's value as expires_at; receiver/app.py just
    # compares expires_at against the current time, no TTL math of its own.
    ACCESS_REQUEST_BUTTON_TTL_SECONDS: int = 3600

    SCHEDULE_TOOLS: frozenset[str] = frozenset({"list_schedules", "add_schedule", "update_schedule", "remove_schedule"})
    DATA_ACCESS_TOOLS: frozenset[str] = frozenset({"request_data_access"})
