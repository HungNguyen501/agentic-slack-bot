"""The skill names a `skill:` binding can match, read from the `agent_skills` table."""
import psycopg

from characters.core.errors import CharacterStorageError
from connectors.db import agent_skills


class SupabaseKnownSkills:
    def names(self) -> frozenset[str]:
        """The names of the stored skills.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """
        try:
            return frozenset(skill.name for skill in agent_skills.get_all())
        except psycopg.Error as exc:
            raise CharacterStorageError(f"Could not read the skills: {exc}") from exc
