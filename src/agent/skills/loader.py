"""Skill loading from Supabase and system-prompt assembly."""
from datetime import date, timedelta
from typing import NamedTuple

from agent.skills.router import select_skills
from connectors.db import agent_skills
from connectors.db.agent_skills import AgentSkill


def load_all_skills() -> list[AgentSkill]:
    """Load all skills from the Supabase agent_skills table, sorted by filename.

    The table is kept in step with the skill files by `make skills-sync` (CI runs it on pushes to master), so a skill edit
    reaches running services without a new image.

    Returns:
        The stored skills, ordered by filename.

    Raises:
        RuntimeError: If the table is empty, which would otherwise run the agent with no instructions.
    """
    skills = agent_skills.get_all()
    if not skills:
        raise RuntimeError("agent_skills is empty; run `make skills-sync`")
    return skills


class SystemPrompt(NamedTuple):
    """The assembled prompt and the names of the skills it was built from, which decide the character that speaks."""

    text: str
    routed: tuple[str, ...]
    always: tuple[str, ...]


def load_system_prompt(
    question: str,
    history: list[dict] | None = None,
    enabled_skills: list[str] | None = None,
) -> SystemPrompt:
    """Build the system prompt by combining always-on skills with router-selected domain skills.

    Args:
        question: The user's question, used by the skill router to pick relevant domain skills.
        history: Recent conversation messages passed to the router for follow-up context.
        enabled_skills: If non-empty, restricts selectable skills to this allowlist. An empty
            list (the default) means all skills are available.

    Returns:
        The assembled system prompt text, including today's date, the 180-day window cutoff and the concatenated
        bodies of all active skill files, with the names of the router-selected and always-loaded skills it used.
    """
    today = date.today()
    cutoff = today - timedelta(days=180)

    all_skills = load_all_skills()
    always_skills = [s for s in all_skills if s.always]
    selectable = [s for s in all_skills if not s.always]

    if enabled_skills:
        selectable = [s for s in selectable if s.name in enabled_skills]

    selected_names = select_skills(question, selectable, history)
    active = always_skills + [s for s in selectable if s.name.lower() in selected_names]

    base = "\n\n---\n\n".join(s.body for s in active)
    text = (
        f"Today's date: {today.isoformat()}\n"
        f"180-day window cutoff: {cutoff.isoformat()} — any date on or after this is within the allowed window.\n\n"
        f"{base}"
    )
    return SystemPrompt(text, tuple(s.name for s in active if not s.always), tuple(s.name for s in always_skills))
