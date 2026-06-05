"""Bot registry — load BotConfig from the Supabase bots table."""
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("connectors.bots")

_DB_URL = os.environ["SUPABASE_DB_URL"]


@dataclass
class BotConfig:
    bot_id: str
    bot_token: str
    signing_secret: bytes
    enabled_skills: list[str] = field(default_factory=list)
    admin_users: frozenset[str] = field(default_factory=frozenset)
    workspace_id: str | None = None


def _connect():
    return psycopg.connect(_DB_URL, row_factory=dict_row)


def _row_to_config(row: Any) -> BotConfig:
    return BotConfig(
        bot_id=str(row["id"]),
        bot_token=str(row["bot_token"]),
        signing_secret=str(row["signing_secret"]).encode(),
        enabled_skills=list(row.get("enabled_skills") or []),
        admin_users=frozenset(row.get("admin_users") or []),
        workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
    )


def get_by_workspace(workspace_id: str) -> BotConfig | None:
    """Return the active bot for a Slack workspace, or None if not found."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bots WHERE workspace_id = %s AND active = TRUE LIMIT 1",
            (workspace_id,),
        )
        row = cur.fetchone()
        return _row_to_config(row) if row else None


def get_by_id(bot_id: str) -> BotConfig:
    """Load a bot config by id; raises ValueError if not found."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bots WHERE id = %s AND active = TRUE",
            (bot_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"No active bot found for id={bot_id!r}")
        return _row_to_config(row)
