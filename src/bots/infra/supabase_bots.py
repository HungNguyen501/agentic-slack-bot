"""Bot registry in the Supabase bots table; the token and signing secret are read from Supabase Vault."""
from typing import Any

from bots.core.errors import BotNotFoundError
from bots.core.models import BotConfig
from connectors.db.connection import connect

# Secrets live in Supabase Vault (see V7 migration); inner joins mean a bot whose secret id
# no longer resolves is treated as not found rather than half-configured.
_SELECT_BOT = """
    SELECT b.id, b.enabled_skills, b.admin_users, b.app_id,
           token.decrypted_secret AS bot_token,
           signing.decrypted_secret AS signing_secret
    FROM bots b
    JOIN vault.decrypted_secrets token ON token.id = b.bot_token_secret_id
    JOIN vault.decrypted_secrets signing ON signing.id = b.signing_secret_secret_id
"""


def _row_to_config(row: Any) -> BotConfig:
    return BotConfig(
        bot_id=str(row["id"]),
        bot_token=str(row["bot_token"]),
        signing_secret=str(row["signing_secret"]).encode(),
        enabled_skills=list(row.get("enabled_skills") or []),
        admin_users=frozenset(row.get("admin_users") or []),
        app_id=str(row["app_id"]) if row.get("app_id") else None,
    )


class SupabaseBotRepository:
    def get_by_app_id(self, app_id: str) -> BotConfig | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(f"{_SELECT_BOT} WHERE b.app_id = %s AND b.active = TRUE LIMIT 1", (app_id,))
            row = cur.fetchone()
            return _row_to_config(row) if row else None

    def get_by_id(self, bot_id: str) -> BotConfig:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(f"{_SELECT_BOT} WHERE b.id = %s AND b.active = TRUE", (bot_id,))
            row = cur.fetchone()
            if not row:
                raise BotNotFoundError(f"No active bot found for id={bot_id!r}")
            return _row_to_config(row)
