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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("receiver")

SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode()
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue(name="slack_events", connection=redis_conn)

app = FastAPI()


def verify_slack_signature(timestamp: str, signature: str, body: bytes) -> bool:
    """Reject requests with an invalid HMAC-SHA256 signature or a timestamp older than 5 minutes.

    Args:
        timestamp: X-Slack-Request-Timestamp header value.
        signature: X-Slack-Signature header value (e.g. v0=abc123...).
        body: Raw request bytes used to recompute the expected signature.

    Returns:
        True if the signature is valid and the request is fresh.
    """
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts) > 300:
        return False

    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(SLACK_SIGNING_SECRET, basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def already_seen(event_id: str) -> bool:
    """Atomic Redis SET NX — returns True if the event_id was already recorded.

    Args:
        event_id: Slack event_id from the event_callback payload.

    Returns:
        True if the event was already processed; False if it is new.
    """
    was_new = redis_conn.set(f"seen:{event_id}", "1", nx=True, ex=600)
    return not was_new


@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack event callbacks — deduplicate, drop bot messages, and enqueue app_mention jobs.

    Args:
        request: Incoming FastAPI request containing the raw Slack event payload.
    """
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(timestamp, signature, body):
        log.warning("Invalid Slack signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)

    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id")
        event = payload.get("event", {})

        if event_id and already_seen(event_id):
            log.info("Duplicate event %s — skipped", event_id)
            return {"ok": True}

        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return {"ok": True}

        if event.get("type") == "app_mention":
            job = queue.enqueue(
                "worker.tasks.reply_to_mention",
                channel=event["channel"],
                thread_ts=event.get("thread_ts") or event["ts"],
                user=event.get("user"),
                text=event.get("text", ""),
                job_timeout=120,
                retry=Retry(max=3, interval=[10, 30, 60]),
            )
            log.info("Enqueued job %s for event %s", job.id, event_id)

    return {"ok": True}


@app.get("/healthz")
def healthz():
    """Ping Redis to confirm the connection is alive before returning 200."""
    redis_conn.ping()
    return {"ok": True}
