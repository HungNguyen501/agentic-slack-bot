"""Scheduler service — polls Supabase every SCHEDULER_INTERVAL seconds and enqueues due jobs."""
import hashlib
import logging
import time
from datetime import datetime, UTC

import redis
import rq
from croniter import croniter

from common.configs import Configs
from connectors.db.schedules import Schedule, get_schedules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")


def _schedule_key(entry: Schedule) -> str:
    """Derive a stable Redis key from a schedule entry for deduplication tracking.

    Args:
        entry: Schedule record.

    Returns:
        A Redis key string in the form scheduler:last_fired:<16-char sha256 hex>.
    """
    raw = f"{entry.cron}:{entry.channel}:{entry.question}"
    return f"scheduler:last_fired:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _should_fire(entry: Schedule, now: datetime, redis_client: redis.Redis) -> bool:
    """Decide whether a schedule entry is due and has not yet been enqueued for this firing.

    Args:
        entry: Schedule record.
        now: Current UTC time as a naive datetime (timezone info stripped by the caller).
        redis_client: Redis connection used to read the last-fired timestamp.

    Returns:
        True if the entry's cron fired within the last SCHEDULER_INTERVAL seconds and no
        enqueue has been recorded for that firing; False otherwise.
    """
    try:
        cron = croniter(entry.cron, now)
        last_expected: datetime = cron.get_prev(datetime)
    except Exception as exc:
        log.warning("Invalid cron expression %r: %s", entry.cron, exc)
        return False

    if (now - last_expected).total_seconds() > Configs.SCHEDULER_INTERVAL:
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
    Sleeps SCHEDULER_INTERVAL seconds between ticks; continues on Supabase read errors.
    """
    redis_client = redis.from_url(Configs.REDIS_URL)
    queue = rq.Queue("slack_events", connection=redis_client)

    log.info("Scheduler started — interval=%ds", Configs.SCHEDULER_INTERVAL)

    while True:
        now = datetime.now(UTC)

        try:
            schedules = get_schedules()
        except Exception as exc:
            log.error("Failed to load schedules from Supabase: %s", exc)
            time.sleep(Configs.SCHEDULER_INTERVAL)
            continue
        log.info("Scanning %d schedule(s) at %s", len(schedules), now.isoformat())

        for entry in schedules:
            if _should_fire(entry, now.replace(tzinfo=None), redis_client):
                key = _schedule_key(entry)
                redis_client.set(key, now.replace(tzinfo=None).isoformat(), ex=86400)

                # Use the schedule's bot_id if set; fall back to "default" (env-var bot)
                bot_id = entry.bot_id or "default"

                queue.enqueue(
                    "worker.tasks.process_scheduled_question",
                    channel=entry.channel,
                    question=entry.question,
                    bot_id=bot_id,
                    job_timeout=120,
                    retry=rq.Retry(max=3, interval=[10, 30, 60]),
                )
                log.info(
                    "Enqueued scheduled question  channel=%s  cron=%r  bot_id=%s",
                    entry.channel,
                    entry.cron,
                    bot_id,
                )

        time.sleep(Configs.SCHEDULER_INTERVAL)


if __name__ == "__main__":
    run()
