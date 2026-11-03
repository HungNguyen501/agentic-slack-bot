"""LLM skill router — picks which domain skills are relevant to a question."""
import json
import logging
import traceback

from common.configs import Configs
from connectors import openai_client

log = logging.getLogger("agent.skills.router")


def _format_history(history: list[dict] | None) -> str:
    """Render the last few turns as router context, or "" when there is nothing usable."""
    lines = [
        f"{m['role'].capitalize()}: {str(m.get('content', ''))[:300]}"
        for m in (history or [])[-10:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return "\n\nPrior conversation (for context only):\n" + "\n".join(lines) if lines else ""


def _router_prompt(selectable: list[dict], history: list[dict] | None) -> str:
    skill_menu = "\n".join(f'- "{s["name"]}": {s["description"]}' for s in selectable)
    return (
        "You are a skill router for a Databricks data assistant. "
        "Given a user question, return a JSON object with key \"skills\" "
        "containing an array of skill names needed to answer it. "
        "Include every skill that could be relevant — when in doubt, include it.\n\n"
        "Available skills:\n" + skill_menu + _format_history(history)
    )


def _parse_selection(content: str, available: set[str]) -> set[str]:
    """Extract the router's picks, keeping only names that exist.

    JSON-object mode guarantees valid JSON, not this schema, so a reply like {"skills": "metadata"}
    or {"skills": ["unknown"]} has to be filtered here — otherwise it counts as a non-empty
    selection and the request runs without any domain instructions.

    Args:
        content: The router model's raw JSON reply.
        available: Lowercased names of the skills that may be selected.

    Returns:
        The valid, lowercased skill names; empty if the reply is malformed or names nothing available.
    """
    data = json.loads(content)
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        return set()
    return {s.strip().lower() for s in skills if isinstance(s, str)} & available


def select_skills(question: str, selectable: list[dict], history: list[dict] | None = None) -> set[str]:
    """Use a lightweight router LLM call to pick which domain skills are relevant for this question.

    Args:
        question: The user's question, with @mention prefix already stripped.
        selectable: Routed (non-always) skill dicts to choose from.
        history: Recent conversation messages used to resolve vague follow-up questions.

    Returns:
        Set of lowercased skill names to activate; falls back to all selectable skill names if the
        router fails or returns nothing valid.
    """
    if not selectable:
        return set()

    available = {s["name"].lower() for s in selectable}
    try:
        content = openai_client.chat_completion(
            [
                {"role": "system", "content": _router_prompt(selectable, history)},
                {"role": "user", "content": question},
            ],
            model=Configs.ROUTER_MODEL,
            json_object=True,
            # Reasoning tokens count against max_completion_tokens, so the cap must leave room for them.
            max_completion_tokens=1000,
            reasoning_effort="low",
        )
        selected = _parse_selection(content, available)
    except Exception:
        log.error("Skill router failed, falling back to all skills:\n%s", traceback.format_exc())
        return available

    if not selected:
        log.warning("Router returned no valid skills; falling back to all skills")
        return available
    log.info("Router selected skills: %s", selected)
    return selected
