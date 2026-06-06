"""Slack webhook receiver — verify signatures, deduplicate events, enqueue work."""
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from redis import Redis
from rq import Queue, Retry

from connectors.bots import BotConfig, get_by_app_id

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


@app.get("/healthz")
def healthz():
    """Ping Redis to confirm the connection is alive before returning 200."""
    redis_conn.ping()
    return {"ok": True}
