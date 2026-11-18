from unittest.mock import patch

import psycopg
import pytest

from characters.core.errors import CharacterStorageError
from characters.infra.known_skills import SupabaseKnownSkills
from connectors.db.agent_skills import AgentSkill


def test_the_known_skills_are_the_names_in_the_agent_skills_table():
    with patch("characters.infra.known_skills.agent_skills", autospec=True) as table:
        table.get_all.return_value = [AgentSkill(name="core", filename="01_core.md", body=""), AgentSkill(name="metadata", filename="03_metadata.md", body="")]

        assert SupabaseKnownSkills().names() == frozenset({"core", "metadata"})


def test_a_database_error_becomes_a_storage_error():
    with patch("characters.infra.known_skills.agent_skills", autospec=True) as table, pytest.raises(CharacterStorageError, match="Could not read the skills"):
        table.get_all.side_effect = psycopg.OperationalError("down")

        SupabaseKnownSkills().names()
