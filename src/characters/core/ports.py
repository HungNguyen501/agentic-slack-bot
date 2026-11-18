from typing import Protocol

from characters.core.models import Character


class CharacterRepository(Protocol):
    def list_all(self) -> list[Character]:
        """Every stored character by name, enabled or not, whether or not its fields are valid.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """

    def get(self, name: str) -> Character | None:
        """One stored character, or None if no character has this name.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """

    def replace(self, character: Character) -> bool:
        """Overwrite every field of the stored character with the same name, but only if its `updated_at` still equals the
        one on `character` (the value read earlier); False if no row matched, because it was deleted or changed since.

        Raises:
            CharacterStorageError: the table couldn't be written.
        """

    def delete(self, name: str) -> bool:
        """Delete the character with this name; False if none is stored.

        Raises:
            CharacterStorageError: the table couldn't be written.
        """

    def add_missing(self, characters: list[Character]) -> list[str]:
        """Insert the characters whose name isn't stored yet, in one step, and return those names. Existing rows are untouched.

        Raises:
            CharacterStorageError: the table couldn't be written, so nothing was added.
        """


class CharacterSource(Protocol):
    def read(self) -> list[Character]:
        """The characters to seed an empty table with.

        Raises:
            InvalidSeedError: a file can't be read or parsed.
        """


class KnownSkills(Protocol):
    def names(self) -> frozenset[str]:
        """The names of the agent skills that exist, which a `skill:` binding can match."""


class ThreadCharacterStore(Protocol):
    """Which character speaks in a thread, so a thread keeps one voice."""

    def get(self, channel: str, thread_ts: str) -> str | None:
        """The character name stored for the thread, or None.

        Raises:
            CharacterStorageError: the store couldn't be read.
        """

    def set(self, channel: str, thread_ts: str, name: str) -> None:
        """Store the character name for the thread.

        Raises:
            CharacterStorageError: the store couldn't be written.
        """
