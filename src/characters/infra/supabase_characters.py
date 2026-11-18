"""The `characters` table."""
import psycopg
from psycopg.types.json import Jsonb

from characters.core.errors import CharacterStorageError
from characters.core.models import Character
from connectors.db.connection import connect

_COLUMNS = "name, display_name, song, artist, enabled, bindings, icon_emoji, icon_url, persona, voices, updated_at"

# The columns a character's fields are written to, in the order `_values` returns them. `name` and `updated_at` are not
# among them: they identify the row, and each query places them itself.
_FIELD_COLUMNS = ("display_name", "song", "artist", "enabled", "bindings", "icon_emoji", "icon_url", "persona", "voices")


def _row_to_character(row: dict) -> Character:
    return Character(
        name=row["name"],
        display_name=row["display_name"],
        bindings=tuple(row["bindings"] or ()),
        persona=row["persona"],
        voices=row["voices"] or {},
        song=row["song"],
        artist=row["artist"],
        enabled=row["enabled"],
        icon_emoji=row["icon_emoji"],
        icon_url=row["icon_url"],
        updated_at=row["updated_at"],
    )


def _values(character: Character) -> tuple:
    """The character's fields as query parameters, in `_FIELD_COLUMNS` order."""
    return (
        character.display_name,
        character.song,
        character.artist,
        character.enabled,
        list(character.bindings),
        character.icon_emoji,
        character.icon_url,
        character.persona,
        Jsonb(dict(character.voices)),
    )


class SupabaseCharacterRepository:
    def list_all(self) -> list[Character]:
        """Every stored character by name, enabled or not.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {_COLUMNS} FROM characters ORDER BY name")
                return [_row_to_character(row) for row in cur.fetchall()]
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not read the characters: {exc}") from exc

    def get(self, name: str) -> Character | None:
        """One stored character, or None.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {_COLUMNS} FROM characters WHERE name = %s", (name,))
                row = cur.fetchone()
                return _row_to_character(row) if row else None
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not read the character: {exc}") from exc

    def replace(self, character: Character) -> bool:
        """Overwrite every field of the stored character with the same name, if its `updated_at` is still the one on `character`.

        Raises:
            CharacterStorageError: the table couldn't be written.
        """
        assignments = ", ".join(f"{column} = %s" for column in _FIELD_COLUMNS)
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"UPDATE characters SET {assignments} WHERE name = %s AND updated_at = %s",
                    (*_values(character), character.name, character.updated_at),
                )
                return cur.rowcount > 0
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not change the character: {exc}") from exc

    def delete(self, name: str) -> bool:
        """Delete the character with this name; False if none is stored.

        Raises:
            CharacterStorageError: the table couldn't be written.
        """
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM characters WHERE name = %s", (name,))
                return cur.rowcount > 0
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not remove the character: {exc}") from exc

    def add_missing(self, characters: list[Character]) -> list[str]:
        """Insert the characters whose name isn't stored yet, in one transaction, and return those names.

        Raises:
            CharacterStorageError: the table couldn't be written, so nothing was added.
        """
        columns = ", ".join(("name", *_FIELD_COLUMNS))
        placeholders = ", ".join("%s" for _ in range(len(_FIELD_COLUMNS) + 1))
        insert = f"INSERT INTO characters ({columns}) VALUES ({placeholders}) ON CONFLICT (name) DO NOTHING RETURNING name"
        added = []
        try:
            with connect() as conn, conn.cursor() as cur:
                # One INSERT per character could cross psycopg's auto-prepare threshold, which a transaction-mode pooler can reject.
                conn.prepare_threshold = None
                for character in characters:
                    cur.execute(insert, (character.name, *_values(character)))
                    if cur.fetchone() is not None:
                        added.append(character.name)
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not store the characters: {exc}") from exc
        return added
