from agent.skills.source import parse_skill, read_skill_files
from connectors.db.agent_skills import AgentSkill


def test_parse_skill_reads_frontmatter_and_body():
    skill = parse_skill("03_metadata.md", "---\nname: metadata\nalways: true\ndescription: Use when asked about tables.\n---\n\nBody text\n")

    assert skill == AgentSkill(name="metadata", filename="03_metadata.md", body="Body text", always=True, description="Use when asked about tables.")


def test_parse_skill_without_frontmatter_uses_filename_as_name():
    skill = parse_skill("x.md", "just text")

    assert (skill.name, skill.always, skill.body) == ("x.md", False, "just text")


def test_shipped_skill_files_all_parse_with_a_name_and_a_body():
    skills = read_skill_files()

    assert skills
    assert all(s.name != s.filename and s.body for s in skills)
