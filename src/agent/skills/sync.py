"""Push the skill files to the Supabase agent_skills table: `make skills-sync` (CI runs it on pushes to master)."""
import logging
from collections import Counter

from agent.skills.source import read_skill_files
from connectors.db import agent_skills
from connectors.db.agent_skills import AgentSkill

log = logging.getLogger("agent.skills.sync")


def collect_skills() -> list[AgentSkill]:
    """Read the skill files and check that they can be stored.

    Raises:
        ValueError: If there are no files, a file has no frontmatter name (or a routed one no description), or two files share a name.
    """
    skills = read_skill_files()
    if not skills:
        raise ValueError("No skill files found")
    # Only routed skills need a description (the router reads it); always-loaded ones have none.
    problems = [
        f"{s.filename}: frontmatter needs a name, and a description unless always: true"
        for s in skills
        if s.name == s.filename or not (s.always or s.description)
    ]
    problems += [f"duplicate skill name {name!r}" for name, n in Counter(s.name for s in skills).items() if n > 1]
    if problems:
        raise ValueError("Invalid skill files:\n" + "\n".join(problems))
    return skills


def main() -> None:
    skills = collect_skills()
    removed = agent_skills.replace_all(skills)
    log.info("Synced %d skills to Supabase (%d removed)", len(skills), removed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
