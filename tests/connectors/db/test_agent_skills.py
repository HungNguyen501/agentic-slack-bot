from functools import partial

import pytest

from connectors.db import agent_skills
from connectors.db.agent_skills import AgentSkill
from tests.support.db import fake_connect as shared_fake_connect

ROW = {"name": "metadata", "filename": "03_metadata.md", "always": False, "description": "d", "body": "b"}
SKILL = AgentSkill(name="metadata", filename="03_metadata.md", body="b", description="d")


fake_connect = partial(shared_fake_connect, "connectors.db.agent_skills.connect", rowcount=0)


def test_get_all_returns_skills_in_file_order():
    with fake_connect(fetchall=[ROW]) as cur:
        assert agent_skills.get_all() == [SKILL]

    assert 'ORDER BY filename COLLATE "C"' in cur.execute.call_args.args[0]


def test_replace_all_upserts_then_deletes_skills_that_are_gone():
    with fake_connect(rowcount=2) as cur:
        removed = agent_skills.replace_all([SKILL])

    assert cur.executemany.call_args.args[1] == [ROW]
    assert cur.execute.call_args.args == ("DELETE FROM agent_skills WHERE name <> ALL(%s)", (["metadata"],))
    assert removed == 2


def test_replace_all_refuses_an_empty_list_instead_of_wiping_the_table():
    with fake_connect() as cur, pytest.raises(ValueError):
        agent_skills.replace_all([])

    cur.execute.assert_not_called()
