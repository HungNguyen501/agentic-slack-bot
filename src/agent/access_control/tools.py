"""Data access request tool handler — posts the "Open Form" button once eligibility checks pass."""
import json
import logging
import time

from agent.access_control.access_request_categories import channel_authorized, get_access_request_category
from common.configs import AgentConfigs
from connectors import slack
from connectors.db.bots import BotConfig

log = logging.getLogger("agent.access_control.tools")

ACCESS_REQUEST_POSTED_MESSAGE = (
    "Click below to fill out a data access request. "
    f"This link expires in {int(AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS / 60)} minutes."
)


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

    button_value = json.dumps(
        {
            "request_type": request_type,
            "channel": channel,
            "thread_ts": thread_ts,
            "requester_id": user_id,
            "reviewers": category.reviewers,
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS,
        }
    )
    try:
        slack.post_message(
            channel,
            ACCESS_REQUEST_POSTED_MESSAGE,
            thread_ts,
            blocks=[
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
                }
            ],
            token=bot.bot_token,
        )
    except Exception as exc:
        log.exception("Failed to post data access request button")
        return f"Error opening the data access request form: {exc}"

    return ACCESS_REQUEST_POSTED_MESSAGE
