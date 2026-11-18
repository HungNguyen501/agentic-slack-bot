"""The `characters` table."""
import psycopg
from psycopg.types.json import Jsonb

from characters.core.errors import CharacterStorageError
from characters.core.models import Character
from connectors.db.connection import connect

_COLUMNS = "name, display_name, song, artist, enabled, bindings, icon_emoji, icon_url, persona, voices, updated_at"


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
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE characters SET display_name = %s, song = %s, artist = %s, enabled = %s, bindings = %s,
                        icon_emoji = %s, icon_url = %s, persona = %s, voices = %s
                    WHERE name = %s AND updated_at = %s
                    """,
                    (
                        character.display_name,
                        character.song,
                        character.artist,
                        character.enabled,
                        list(character.bindings),
                        character.icon_emoji,
                        character.icon_url,
                        character.persona,
                        Jsonb(dict(character.voices)),
                        character.name,
                        character.updated_at,
                    ),
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
        added = []
        try:
            with connect() as conn, conn.cursor() as cur:
                # One INSERT per character could cross psycopg's auto-prepare threshold, which a transaction-mode pooler can reject.
                conn.prepare_threshold = None
                for character in characters:
                    cur.execute(
                        """
                        INSERT INTO characters (name, display_name, song, artist, enabled, bindings, icon_emoji, icon_url, persona, voices)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (name) DO NOTHING
                        RETURNING name
                        """,
                        (
                            character.name,
                            character.display_name,
                            character.song,
                            character.artist,
                            character.enabled,
                            list(character.bindings),
                            character.icon_emoji,
                            character.icon_url,
                            character.persona,
                            Jsonb(dict(character.voices)),
                        ),
                    )
                    if cur.fetchone() is not None:
                        added.append(character.name)
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not store the characters: {exc}") from exc
        return added
