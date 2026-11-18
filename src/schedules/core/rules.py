def is_same_reminder(existing: dict, new: dict) -> bool:
    """Whether two reminder configs on the same channel are the same job: same scope and the same people, and for a
    thread-scope reminder the same thread. The cron and lookback are not part of the identity."""
    return (
        existing.get("scope") == new["scope"]
        and sorted(existing.get("mentions") or []) == sorted(new["mentions"])
        and (new["scope"] != "thread" or existing.get("origin_thread_ts") == new["origin_thread_ts"])
    )
