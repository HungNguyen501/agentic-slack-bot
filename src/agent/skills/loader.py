"""Skill file loading and system-prompt assembly."""
import os
from datetime import date, timedelta

from agent.skills.router import select_skills

# Only this subfolder is volume-mounted in docker-compose, so the loader code next to it always comes from the image.
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "files")


def parse_skill_file(filename: str) -> dict:
    """Parse YAML frontmatter and body from a skill Markdown file.

    Args:
        filename: Basename of the .md file inside the skills directory.

    Returns:
        Dict with keys: filename, name, always (bool), description, and body (str).
    """
    with open(os.path.join(SKILLS_DIR, filename), encoding="utf-8") as fh:
        content = fh.read()
    meta, body = {}, content
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = parts[2]
    return {
        "filename": filename,
        "name": meta.get("name", filename),
        "always": meta.get("always", "false").lower() == "true",
        "description": meta.get("description", ""),
        "body": body.strip(),
    }


def load_all_skills() -> list[dict]:
    """Load and sort all skill files from the skills directory.

    Returns:
        List of parsed skill dicts sorted by filename, each produced by parse_skill_file.
    """
    return [
        parse_skill_file(f)
        for f in sorted(f for f in os.listdir(SKILLS_DIR) if f.endswith(".md"))
    ]


def load_system_prompt(
    question: str,
    history: list[dict] | None = None,
    enabled_skills: list[str] | None = None,
) -> str:
    """Build the system prompt by combining always-on skills with router-selected domain skills.

    Args:
        question: The user's question, used by the skill router to pick relevant domain skills.
        history: Recent conversation messages passed to the router for follow-up context.
        enabled_skills: If non-empty, restricts selectable skills to this allowlist. An empty
            list (the default) means all skills are available.

    Returns:
        Assembled system prompt string including today's date, the 180-day window cutoff,
        and the concatenated bodies of all active skill files.
    """
    today = date.today()
    cutoff = today - timedelta(days=180)

    all_skills = load_all_skills()
    always_skills = [s for s in all_skills if s["always"]]
    selectable = [s for s in all_skills if not s["always"]]

    if enabled_skills:
        selectable = [s for s in selectable if s["name"] in enabled_skills]

    selected_names = select_skills(question, selectable, history)
    active = always_skills + [s for s in selectable if s["name"].lower() in selected_names]

    base = "\n\n---\n\n".join(s["body"] for s in active)
    return (
        f"Today's date: {today.isoformat()}\n"
        f"180-day window cutoff: {cutoff.isoformat()} — any date on or after this is within the allowed window.\n\n"
        f"{base}"
    )
