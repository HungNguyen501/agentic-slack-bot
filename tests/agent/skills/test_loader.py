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


def _skills():
    return [
        AgentSkill(name="core", filename="01_core.md", body="CORE", always=True),
        AgentSkill(name="formatting", filename="08_formatting.md", body="FORMAT", always=True),
        AgentSkill(name="metadata", filename="03_metadata.md", body="META", description="d"),
        AgentSkill(name="jobs", filename="04_jobs.md", body="JOBS", description="d"),
    ]


def test_the_system_prompt_reports_the_skills_it_was_built_from():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table, patch("agent.skills.loader.select_skills", autospec=True, return_value={"metadata"}):
        table.get_all.return_value = _skills()

        prompt = loader.load_system_prompt("which tables?")

    assert (prompt.routed, prompt.always) == (("metadata",), ("core", "formatting"))
    assert "CORE" in prompt.text and "META" in prompt.text and "JOBS" not in prompt.text


def test_the_system_prompt_reports_no_routed_skill_when_the_router_picks_none():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table, patch("agent.skills.loader.select_skills", autospec=True, return_value=set()):
        table.get_all.return_value = _skills()

        prompt = loader.load_system_prompt("hello")

    assert prompt.routed == ()
    assert prompt.always == ("core", "formatting")


def test_a_skill_the_bot_does_not_enable_is_not_reported_as_routed():
    with patch("agent.skills.loader.agent_skills", autospec=True) as table, patch("agent.skills.loader.select_skills", autospec=True, return_value={"metadata", "jobs"}):
        table.get_all.return_value = _skills()

        prompt = loader.load_system_prompt("q", enabled_skills=["jobs"])

    assert prompt.routed == ("jobs",)
