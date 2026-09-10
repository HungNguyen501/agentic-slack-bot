"""Slack webhook receiver — verify signatures, deduplicate events, enqueue work."""
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from redis import Redis
from rq import Queue, Retry

from connectors import slack
from connectors.bots import BotConfig, get_by_app_id
from models.access_request_view import build_access_request_view

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("receiver")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue(name="slack_events", connection=redis_conn)

app = FastAPI()


def verify_slack_signature(secret: bytes, timestamp: str, signature: str, body: bytes) -> bool:
    """Return True if the HMAC-SHA256 signature is valid and the request is within 5 minutes.

    Args:
        secret: bot signing secret. timestamp: X-Slack-Request-Timestamp.
        signature: X-Slack-Signature (v0=hex). body: raw request bytes.
    """
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts) > 300:
        return False

    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret, basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def already_seen(event_id: str) -> bool:
    """Return True if this event_id was already processed (atomic Redis SET NX).

    Args:
        event_id: Slack event_id from the event_callback payload.
    """
    was_new = redis_conn.set(f"seen:{event_id}", "1", nx=True, ex=600)
    return not was_new


def _resolve_bot(payload: dict) -> BotConfig:
    """Return the bot config for this Slack app; raises HTTPException if not found.

    Args:
        payload: parsed Slack event payload containing api_app_id.
    """
    app_id = payload.get("api_app_id")
    if app_id:
        bot = get_by_app_id(app_id)
        if bot:
            return bot
    raise HTTPException(status_code=500, detail=f"No active bot configured for app {app_id!r}")


@app.post("/slack/events")
async def slack_events(request: Request) -> dict:
    """Verify signature, deduplicate, and enqueue app_mention events.

    Args:
        request: incoming FastAPI request with raw Slack event payload.
    """
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    # Parse first to resolve which bot (and therefore which signing secret) owns this event.
    payload = json.loads(body)
    bot = _resolve_bot(payload)

    if not verify_slack_signature(bot.signing_secret, timestamp, signature, body):
        log.warning("Invalid Slack signature for bot_id=%s", bot.bot_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id")
        event = payload.get("event", {})

        if event_id and already_seen(event_id):
            log.info("Duplicate event %s — skipped", event_id)
            return {"ok": True}

        if event.get("app_id") == bot.app_id or event.get("subtype") == "bot_message":
            return {"ok": True}

        if event.get("type") == "app_mention":
            job = queue.enqueue(
                "worker.tasks.reply_to_mention",
                channel=event["channel"],
                thread_ts=event.get("thread_ts") or event["ts"],
                user=event.get("user"),
                text=event.get("text", ""),
                bot_id=bot.bot_id,
                job_timeout=120,
                retry=Retry(max=3, interval=[10, 30, 60]),
            )
            log.info("Enqueued job %s for event %s (bot_id=%s)", job.id, event_id, bot.bot_id)

    return {"ok": True}


def _extract_view_submission_fields(view: dict) -> dict:
    """Flatten a Slack view's state.values into a plain {block_id: value} dict.

    Args:
        view: The "view" object from a view_submission payload.

    Returns:
        Dict mapping each input block's block_id to its submitted value — a string for
        plain_text_input/static_select, or a list of strings for multi_static_select.
    """
    fields = {}
    for block_id, actions in view.get("state", {}).get("values", {}).items():
        action = next(iter(actions.values()))
        if "selected_option" in action:
            fields[block_id] = (action["selected_option"] or {}).get("value")
        elif "selected_options" in action:
            fields[block_id] = [o["value"] for o in action["selected_options"]]
        else:
            fields[block_id] = action.get("value")
    return fields


@app.post("/slack/interactivity")
async def slack_interactivity(request: Request) -> dict:
    """Verify signature and handle Slack interactive payloads (block_actions, view_submission).

    Args:
        request: incoming FastAPI request with a form-urlencoded `payload` field.
    """
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    form = parse_qs(body.decode())
    payload = json.loads(form["payload"][0])
    bot = _resolve_bot(payload)

    if not verify_slack_signature(bot.signing_secret, timestamp, signature, body):
        log.warning("Invalid Slack signature for bot_id=%s", bot.bot_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload_type = payload.get("type")

    if payload_type == "block_actions":
        action = payload["actions"][0]
        if action.get("action_id") == "open_access_request_form":
            value = json.loads(action["value"])
            # views.open must be called within ~3s of the trigger_id being issued — the RQ
            # queue can't guarantee that latency under load, so this is called directly here
            # rather than enqueued (see architecture.md's documented exception for this route).
            view = build_access_request_view(value["request_type"], private_metadata=action["value"])
            slack.open_view(payload["trigger_id"], view, token=bot.bot_token)
        return {}

    if payload_type == "view_submission":
        view = payload.get("view", {})
        metadata = json.loads(view.get("private_metadata") or "{}")
        job = queue.enqueue(
            "worker.tasks.process_data_access_submission",
            bot_id=bot.bot_id,
            channel=metadata.get("channel"),
            thread_ts=metadata.get("thread_ts"),
            requester_id=metadata.get("requester_id"),
            request_type=metadata.get("request_type"),
            reviewers=metadata.get("reviewers") or [],
            fields=_extract_view_submission_fields(view),
            job_timeout=30,
        )
        log.info("Enqueued job %s for data access submission (bot_id=%s)", job.id, bot.bot_id)
        return {}

    return {}


@app.get("/healthz")
def healthz():
    """Ping Redis to confirm the connection is alive before returning 200."""
    redis_conn.ping()
    return {"ok": True}
