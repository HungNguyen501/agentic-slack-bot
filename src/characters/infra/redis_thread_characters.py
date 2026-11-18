"""Which character speaks in a thread, in Redis (`thread_character:{channel}:{thread_ts}`)."""
from redis import Redis, RedisError

from characters.core.errors import CharacterStorageError


class RedisThreadCharacterStore:
    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(channel: str, thread_ts: str) -> str:
        return f"thread_character:{channel}:{thread_ts}"

    def get(self, channel: str, thread_ts: str) -> str | None:
        """The character name stored for the thread, or None.

        Raises:
            CharacterStorageError: Redis couldn't be reached.
        """
        try:
            raw = self._client.get(self._key(channel, thread_ts))
        except RedisError as exc:
            raise CharacterStorageError(f"Could not read the thread character: {exc}") from exc
        return raw.decode() if raw else None

    def set(self, channel: str, thread_ts: str, name: str) -> None:
        """Store the character name for the thread for the configured time.

        Raises:
            CharacterStorageError: Redis couldn't be reached.
        """
        try:
            self._client.set(self._key(channel, thread_ts), name, ex=self._ttl_seconds)
        except RedisError as exc:
            raise CharacterStorageError(f"Could not store the thread character: {exc}") from exc
