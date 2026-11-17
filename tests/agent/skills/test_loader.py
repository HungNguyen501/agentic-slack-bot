from unittest.mock import patch

import pytest

from agent.skills import loader
from connectors.db.agent_skills import AgentSkill

SKILL = AgentSkill(name="core", filename="01_core.md", body="from db", always=True)


def test_reads_skills_from_supabase():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table:
        table.get_all.return_value = [SKILL]

        assert loader.load_all_skills() == [SKILL]


def test_an_empty_table_fails_instead_of_running_the_agent_without_instructions():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table, pytest.raises(RuntimeError, match="make skills-sync"):
        table.get_all.return_value = []
        loader.load_all_skills()


def test_a_supabase_error_propagates():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table, pytest.raises(ConnectionError):
        table.get_all.side_effect = ConnectionError("down")
        loader.load_all_skills()
