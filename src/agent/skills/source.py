"""Skill Markdown files — parsing and reading them from disk; `agent.skills.sync` pushes them to Supabase."""
import os

from connectors.db.agent_skills import AgentSkill


class SkillSourceConfigs:
    """Where the agent skill files are read from."""

    SKILLS_DIR = os.path.join(os.path.dirname(__file__), "files")


def parse_skill(filename: str, content: str) -> AgentSkill:
    """Parse YAML frontmatter and body from the text of a skill Markdown file.

    Args:
        filename: Basename of the .md file, used as the name when the frontmatter has none.
        content: Full text of the file.

    Returns:
        The skill; its name is the filename when the frontmatter has none.
    """
    meta, body = {}, content
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = parts[2]
    return AgentSkill(
        name=meta.get("name", filename),
        filename=filename,
        body=body.strip(),
        always=meta.get("always", "false").lower() == "true",
        description=meta.get("description", ""),
    )


def parse_skill_file(filename: str) -> AgentSkill:
    """Read one skill file from the skills directory and parse it with parse_skill."""
    with open(os.path.join(SkillSourceConfigs.SKILLS_DIR, filename), encoding="utf-8") as fh:
        return parse_skill(filename, fh.read())


def read_skill_files() -> list[AgentSkill]:
    """Read and sort all skill files from the skills directory.

    Returns:
        List of parsed skills sorted by filename, each produced by parse_skill_file.
    """
    return [parse_skill_file(f) for f in sorted(f for f in os.listdir(SkillSourceConfigs.SKILLS_DIR) if f.endswith(".md"))]
