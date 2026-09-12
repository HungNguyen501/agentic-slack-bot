"""Slack Web API client — message posting and modal views."""
from slack_sdk import WebClient


def post_message(channel: str, text: str, thread_ts: str | None = None, blocks: list[dict] | None = None, *, token: str) -> str:
    """Post a message to Slack and return its timestamp.

    Args:
        channel: Slack channel ID to post into.
        text: Message body in Slack mrkdwn format (also used as the notification fallback text when blocks are set).
        thread_ts: If provided, posts as a reply in that thread; otherwise posts a new top-level message.
        blocks: Optional Block Kit blocks to render instead of/alongside plain text.
        token: Bot OAuth token (xoxb-...) to authenticate the request.

    Returns:
        The Slack message timestamp (ts) of the posted message.
    """
    response = WebClient(token=token).chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, blocks=blocks)
    return str(response["ts"])


def update_message(channel: str, ts: str, text: str, *, token: str) -> None:
    """Edit an existing Slack message in place.

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to update.
        text: New message body in Slack mrkdwn format.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).chat_update(channel=channel, ts=ts, text=text)


def delete_message(channel: str, ts: str, *, token: str) -> None:
    """Delete an existing Slack message (e.g. a "thinking..." placeholder no longer needed).

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to delete.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).chat_delete(channel=channel, ts=ts)


def open_view(trigger_id: str, view: dict, *, token: str) -> None:
    """Open a Slack modal. Must be called within ~3 seconds of the trigger_id being issued.

    Args:
        trigger_id: Trigger ID from the interaction (e.g. a button click) that authorizes the modal.
        view: Slack view payload, e.g. as built by models.access_request_view.build_access_request_view.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    WebClient(token=token).views_open(trigger_id=trigger_id, view=view)
