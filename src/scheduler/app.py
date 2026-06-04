"""Scheduler service — polls Supabase every CHECK_INTERVAL seconds and enqueues due jobs."""
import hashlib
import logging
import os
import time
from datetime import datetime, UTC

import redis
import rq
from croniter import croniter

from connectors.postgres import get_schedules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

CHECK_INTERVAL = int(os.environ.get("SCHEDULER_INTERVAL", "180"))
REDIS_URL = os.environ["REDIS_URL"]


def _schedule_key(entry: dict) -> str:
    """Derive a stable Redis key from a schedule entry for deduplication tracking.

    Args:
        entry: Schedule dict containing cron, channel, and question fields.

    Returns:
        A Redis key string in the form scheduler:last_fired:<16-char sha256 hex>.
    """
    raw = f"{entry['cron']}:{entry['channel']}:{entry['question']}"
    return f"scheduler:last_fired:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _should_fire(entry: dict, now: datetime, redis_client: redis.Redis) -> bool:
    """Decide whether a schedule entry is due and has not yet been enqueued for this firing.

    Args:
        entry: Schedule dict with at least cron, channel, and question fields.
        now: Current UTC time as a naive datetime (timezone info stripped by the caller).
        redis_client: Redis connection used to read the last-fired timestamp.

    Returns:
        True if the entry's cron fired within the last CHECK_INTERVAL seconds and no
        enqueue has been recorded for that firing; False otherwise.
    """
    try:
        cron = croniter(entry["cron"], now)
        last_expected: datetime = cron.get_prev(datetime)
    except Exception as exc:
        log.warning("Invalid cron expression %r: %s", entry.get("cron"), exc)
        return False

    if (now - last_expected).total_seconds() > CHECK_INTERVAL:
        return False

    key = _schedule_key(entry)
    last_fired_raw = redis_client.get(key)
    if last_fired_raw:
        last_fired = datetime.fromisoformat(last_fired_raw.decode())
        if last_fired >= last_expected.replace(tzinfo=None):
            return False

    return True


def run() -> None:
    """Start the scheduler loop — polls Supabase and enqueues due jobs until interrupted.

    Reads all schedules from Supabase on every tick, evaluates each cron expression against
    the current time, and pushes a process_scheduled_question job onto the slack_events
    Redis queue for any entry that is due and has not already been enqueued this cycle.
    Sleeps CHECK_INTERVAL seconds between ticks; continues on Supabase read errors.
    """
    redis_client = redis.from_url(REDIS_URL)
    queue = rq.Queue("slack_events", connection=redis_client)

    log.info("Scheduler started — interval=%ds", CHECK_INTERVAL)

    while True:
        now = datetime.now(UTC)

        try:
            schedules = get_schedules()
        except Exception as exc:
            log.error("Failed to load schedules from Supabase: %s", exc)
            time.sleep(CHECK_INTERVAL)
            continue
        log.info("Scanning %d schedule(s) at %s", len(schedules), now.isoformat())

        for entry in schedules:
            if not all(k in entry for k in ("cron", "channel", "question")):
                log.warning("Skipping entry with missing keys: %r", entry)
                continue

            if _should_fire(entry, now.replace(tzinfo=None), redis_client):
                key = _schedule_key(entry)
                redis_client.set(key, now.replace(tzinfo=None).isoformat(), ex=86400)

                queue.enqueue(
                    "worker.tasks.process_scheduled_question",
                    channel=entry["channel"],
                    question=entry["question"],
                    job_timeout=120,
                    retry=rq.Retry(max=3, interval=[10, 30, 60]),
                )
                log.info(
                    "Enqueued scheduled question  channel=%s  cron=%r",
                    entry["channel"],
                    entry["cron"],
                )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
