"""Slack Web API client — message posting and modal views."""
import httpx


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
    payload: dict = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    resp = httpx.post(
        url="https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")

    return data["ts"]


def update_message(channel: str, ts: str, text: str, *, token: str) -> None:
    """Edit an existing Slack message in place.

    Args:
        channel: Slack channel ID containing the message.
        ts: Timestamp of the message to update.
        text: New message body in Slack mrkdwn format.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    resp = httpx.post(
        url="https://slack.com/api/chat.update",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"channel": channel, "ts": ts, "text": text},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")


def open_view(trigger_id: str, view: dict, *, token: str) -> None:
    """Open a Slack modal. Must be called within ~3 seconds of the trigger_id being issued.

    Args:
        trigger_id: Trigger ID from the interaction (e.g. a button click) that authorizes the modal.
        view: Slack view payload, e.g. as built by models.access_request_view.build_access_request_view.
        token: Bot OAuth token (xoxb-...) to authenticate the request.
    """
    resp = httpx.post(
        url="https://slack.com/api/views.open",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"trigger_id": trigger_id, "view": view},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
