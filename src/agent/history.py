"""Per-thread conversation history, cached in Redis."""
import json

from connectors.redis_client import client as redis_client


def load_history(thread_ts: str) -> list[dict]:
    """Retrieve stored conversation history for a Slack thread from Redis.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.

    Returns:
        List of message dicts in OpenAI chat format (role + content), or an empty list if
        no history exists for the thread.
    """
    raw = redis_client.get(f"chat_history:{thread_ts}")
    return json.loads(raw) if raw else []


def save_history(thread_ts: str, history: list[dict]) -> None:
    """Trim the conversation history to the last 20 pairs and persist it to Redis with a 24-hour TTL.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.
        history: Full list of user/assistant message dicts accumulated during the agent loop.
    """
    history_ttl = 86400  # 24 hours
    max_history_turns = 20  # keep last 20 user/assistant pairs
    max_messages = max_history_turns * 2

    if len(history) > max_messages:
        history = history[-max_messages:]

    redis_client.set(f"chat_history:{thread_ts}", json.dumps(history), ex=history_ttl)


def record_thread_messages(thread_ts: str, *messages: dict) -> None:
    """Append messages posted outside the agent loop (access request review, approve/reject outcomes) to the thread's history.

    Lets a later mention in the same thread see what happened to the thread's data access
    requests, since those flows bypass run_agent and would otherwise leave no trace in history.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.
        *messages: Message dicts in OpenAI chat format (role + content), appended in order.
    """
    history = load_history(thread_ts)
    history.extend(messages)
    save_history(thread_ts, history)
