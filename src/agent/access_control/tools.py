"""Data access request tool handler — posts the "Open Form" button once eligibility checks pass."""
import json
import logging
import secrets
import time

from agent.access_control.access_request_categories import channel_authorized, get_access_request_category
from agent.dataset_sync import store
from agent.dataset_sync.store import DatasetNotSyncedError
from agent.forms.access_request_options import LOCATIONS_DATASET, TAGS_DATASET, FormOptionsError, build_form_options, options_redis_key
from common.configs import AgentConfigs
from connectors import slack
from connectors.db.bots import BotConfig
from connectors.redis_client import client as redis_client

log = logging.getLogger("agent.access_control.tools")

ACCESS_REQUEST_POSTED_MESSAGE = (
    "Click below to fill out a data access request. "
    f"This link expires in {int(AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS / 60)} minutes."
)


def build_form_button_value(
    request_type: str, channel: str, thread_ts: str, requester_id: str | None, reviewers: list[str], options_token: str, now: int
) -> str:
    """Serialize the request metadata carried in the "Open Form" button's `value`.

    Args:
        options_token: Names the Redis snapshot of the form's dropdown options (see `stash_form_options`); the receiver can't read Supabase.
        now: Current unix time; both created_at and expires_at derive from it so they can't drift apart.
    """
    return json.dumps(
        {
            "request_type": request_type,
            "channel": channel,
            "thread_ts": thread_ts,
            "requester_id": requester_id,
            "reviewers": reviewers,
            "options_token": options_token,
            "created_at": now,
            "expires_at": now + AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS,
        }
    )


def build_form_blocks(button_value: str) -> list[dict]:
    """Block Kit blocks for the TTL notice plus the "Open Form" button."""
    return [
        # message_text alone is only the notification fallback once `blocks` is set —
        # Slack renders the blocks array in-channel, so the TTL notice needs its own
        # section block here or it never actually shows up next to the button.
        {"type": "section", "text": {"type": "mrkdwn", "text": ACCESS_REQUEST_POSTED_MESSAGE}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Form"},
                    "action_id": "open_access_request_form",
                    "value": button_value,
                }
            ],
        },
    ]


def stash_form_options() -> str:
    """Snapshot the form's synced dropdown options into Redis for the button about to be posted, and return the token naming it.

    The receiver must open the form within seconds of the click and may not read Supabase, so the worker reads the synced
    datasets here and the receiver only does a Redis GET. The snapshot lives as long as the button does.

    Raises:
        DatasetNotSyncedError: a dataset the form needs has no successful sync yet.
        FormOptionsError: the synced data can't make a working form.
    """
    options = build_form_options(store.get_rows(LOCATIONS_DATASET), store.get_rows(TAGS_DATASET))
    token = secrets.token_urlsafe(16)
    redis_client.set(options_redis_key(token), options.to_json(), ex=AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS)
    return token


def dispatch_data_access_tool(args: dict, user_id: str | None, channel: str, thread_ts: str, bot: BotConfig) -> str:
    """Check eligibility for a data access request and, if allowed, post an interactive form button.

    Args:
        args: Parsed JSON arguments from the LLM tool call; expects "request_type".
        user_id: Slack user ID of the requester, carried through to the form submission.
        channel: Slack channel ID the request was made in — must be whitelisted for this request_type.
        thread_ts: Thread timestamp to post the form button into.
        bot: Per-bot config carrying bot_id (for the eligibility lookup) and bot_token (to post to Slack).

    Returns:
        A confirmation string once the form button is posted, or a polite refusal string if this
        request_type isn't configured or isn't allowed in this channel.
    """
    request_type = args.get("request_type", "")
    try:
        category = get_access_request_category(bot.bot_id, request_type)
    except Exception as exc:
        log.exception("Access request category lookup failed")
        return f"Error checking access-request eligibility: {exc}"

    if not channel_authorized(category, channel):
        return (
            "Sorry, data access requests of this type aren't available in this channel. "
            "Please reach out to your data team directly."
        )

    try:
        options_token = stash_form_options()
    except (DatasetNotSyncedError, FormOptionsError) as exc:
        log.warning("Form options unavailable: %s", exc)
        return (
            f"The request form can't be opened right now: {exc} "
            "An admin can fix this by syncing the datasets it reads (ask the bot to sync them). Please try again afterwards."
        )
    except Exception as exc:
        log.exception("Failed to prepare the data access request form")
        return f"Error preparing the data access request form: {exc}"

    button_value = build_form_button_value(request_type, channel, thread_ts, user_id, category.reviewers, options_token, int(time.time()))
    try:
        slack.post_message(channel, ACCESS_REQUEST_POSTED_MESSAGE, thread_ts, blocks=build_form_blocks(button_value), token=bot.bot_token)
    except Exception as exc:
        log.exception("Failed to post data access request button")
        return f"Error opening the data access request form: {exc}"

    return ACCESS_REQUEST_POSTED_MESSAGE
