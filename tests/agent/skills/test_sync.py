from dataclasses import replace
from unittest.mock import patch

import pytest

from agent.skills import sync
from connectors.db.agent_skills import AgentSkill

SKILL = AgentSkill(name="metadata", filename="03_metadata.md", body="b", description="Use when...")


def collect(skills):
    with patch("agent.skills.sync.read_skill_files", autospec=True, return_value=skills):
        return sync.collect_skills()


def test_collect_skills_returns_valid_skills():
    assert collect([SKILL]) == [SKILL]


def test_collect_skills_accepts_an_always_loaded_skill_without_a_description():
    skill = replace(SKILL, always=True, description="")

    assert collect([skill]) == [skill]


@pytest.mark.parametrize(
    "skills, message",
    [
        ([], "No skill files"),
        ([replace(SKILL, name=SKILL.filename)], "needs a name"),
        ([replace(SKILL, description="")], "needs a name"),
        ([SKILL, replace(SKILL, filename="04_other.md")], "duplicate skill name 'metadata'"),
    ],
)
def test_collect_skills_rejects_files_that_cannot_be_stored(skills, message):
    with pytest.raises(ValueError, match=message):
        collect(skills)


def test_main_replaces_the_table_with_the_files():
    with patch("agent.skills.sync.read_skill_files", autospec=True, return_value=[SKILL]), patch("agent.skills.sync.agent_skills", autospec=True) as table:
        table.replace_all.return_value = 2
        sync.main()

    table.replace_all.assert_called_once_with([SKILL])
