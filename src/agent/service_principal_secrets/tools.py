"""Service-principal secret generation tool handler — mints (or reuses) a secret and hands back a one-time view link."""
import json
import logging
import secrets
from datetime import UTC, datetime

from agent.access_control import principals
from agent.access_control.access_request_categories import channel_authorized, get_access_request_category
from agent.forms.access_request_view import EMAIL_PATTERN
from agent.service_principal_secrets.store import get as get_cached_secret, upsert as upsert_secret
from common import crypto
from common.configs import AgentConfigs, Configs
from connectors import databricks
from connectors.db.bots import BotConfig
from connectors.redis_client import client as redis_client

log = logging.getLogger("agent.service_principal_secrets.tools")


def dispatch_secret_generation_tool(args: dict, user_id: str | None, channel: str, bot: BotConfig) -> str:
    """Check reviewer eligibility, generate (or reuse a cached) Databricks SP secret, and return a one-time view link.

    Args:
        args: Parsed JSON arguments from the LLM tool call; expects "service_account" (bare email).
        user_id: Slack user ID of the requester — must be in the category's reviewers list.
        channel: Slack channel ID the request was made in — must be whitelisted for this request_type.
        bot: Per-bot config carrying bot_id (for the eligibility lookup).

    Returns:
        A message containing a one-time secret-view link, or a polite refusal string if the
        requester/channel isn't eligible or no matching service principal exists.
    """
    email = args.get("service_account", "").strip()
    if not EMAIL_PATTERN.match(email):
        return f"`{email}` doesn't look like a valid email address. Please provide the service principal's email."

    try:
        category = get_access_request_category(bot.bot_id, "service_principal_secret")
    except Exception as exc:
        log.exception("Secret-generation category lookup failed")
        return f"Error checking secret-generation eligibility: {exc}"

    if category is None or not channel_authorized(category, channel):
        return (
            "Sorry, service-principal secret generation isn't available in this channel. "
            "Please reach out to your data team directly."
        )
    if user_id not in category.reviewers:
        return "Sorry, only configured reviewers can generate service-principal secrets."

    try:
        sp = principals.find_service_principal_by_email(email)
    except Exception as exc:
        log.exception("Service principal lookup failed")
        return f"Error looking up service principal: {exc}"

    if sp is None:
        return (
            f"No service principal found for `{email}`. This tool only mints secrets for service principals "
            "that already exist under the `svc-<email>` naming convention created by an approved data access "
            "request — it can't create a new one. If this address needs a service principal, submit a data "
            "access request with principal type \"Service principals\" for it and get it approved first, then "
            "ask again here. If this is an existing Databricks service account for a scheduled job/pipeline "
            "rather than a GPT-user service principal, this tool doesn't apply — reach out to the data team directly."
        )

    cached = get_cached_secret(email)
    now = datetime.now(UTC)

    try:
        if cached and cached.status == "ACTIVE" and cached.dbx_expire_time > now:
            payload = {
                "create_time": cached.dbx_create_time.isoformat(),
                "expire_time": cached.dbx_expire_time.isoformat(),
                "id": cached.dbx_secret_id,
                "secret": crypto.decrypt(cached.secret_encrypted),
                "secret_hash": cached.secret_hash,
                "status": cached.status,
                "update_time": cached.dbx_update_time.isoformat(),
                "service_account": cached.service_account,
                "client_id": cached.client_id,
                "email": cached.email,
            }
        else:
            raw = databricks.create_secret(sp["id"])
            service_account_name = sp.get("displayName", f"svc-{email}")
            client_id = sp.get("applicationId", "")
            upsert_secret(
                service_account=service_account_name,
                client_id=client_id,
                email=email,
                dbx_secret_id=raw["id"],
                secret_hash=raw["secret_hash"],
                secret_encrypted=crypto.encrypt(raw["secret"]),
                status=raw["status"],
                dbx_create_time=datetime.fromisoformat(raw["create_time"]),
                dbx_update_time=datetime.fromisoformat(raw["update_time"]),
                dbx_expire_time=datetime.fromisoformat(raw["expire_time"]),
                requested_by=user_id or "",
            )
            payload = {
                "create_time": raw["create_time"],
                "expire_time": raw["expire_time"],
                "id": raw["id"],
                "secret": raw["secret"],
                "secret_hash": raw["secret_hash"],
                "status": raw["status"],
                "update_time": raw["update_time"],
                "service_account": service_account_name,
                "client_id": client_id,
                "email": email,
            }
    except Exception as exc:
        log.exception("Secret generation/retrieval failed for %s", email)
        return f"Error generating a secret for `{email}`: {exc}"

    token = secrets.token_urlsafe(32)
    redis_client.set(f"secret_link:{token}", json.dumps(payload), ex=AgentConfigs.SECRET_LINK_TTL_SECONDS)

    minutes = AgentConfigs.SECRET_LINK_TTL_SECONDS // 60
    return (
        f"Here's a one-time link to view the secret for `{email}`. "
        f"It can only be opened once and expires in {minutes} minutes: "
        f"{Configs.SECRET_LINK_BASE_URL}/secrets/{token}"
    )
