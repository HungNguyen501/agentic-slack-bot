"""LLM skill router — picks which domain skills are relevant to a question."""
import json
import logging
import traceback

from common.configs import Configs
from connectors import openai_client

log = logging.getLogger("agent.skills.router")


def select_skills(question: str, selectable: list[dict], history: list[dict] | None = None) -> set[str]:
    """Use a lightweight router LLM call to pick which domain skills are relevant for this question.

    Args:
        question: The user's question, with @mention prefix already stripped.
        selectable: Routed (non-always) skill dicts to choose from.
        history: Recent conversation messages used to resolve vague follow-up questions.

    Returns:
        Set of skill name strings to activate; falls back to all selectable skill names on failure.
    """
    if not selectable:
        return set()

    skill_menu = "\n".join(f'- "{s["name"]}": {s["description"]}' for s in selectable)

    context = ""
    if history:
        recent = history[-10:]
        lines = [
            f"{m['role'].capitalize()}: {str(m.get('content', ''))[:300]}"
            for m in recent
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if lines:
            context = "\n\nPrior conversation (for context only):\n" + "\n".join(lines)

    try:
        content = openai_client.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a skill router for a Databricks data assistant. "
                        "Given a user question, return a JSON object with key \"skills\" "
                        "containing an array of skill names needed to answer it. "
                        "Include every skill that could be relevant — when in doubt, include it.\n\n"
                        "Available skills:\n" + skill_menu + context
                    ),
                },
                {"role": "user", "content": question},
            ],
            model=Configs.ROUTER_MODEL,
            json_object=True,
            # Reasoning tokens count against max_completion_tokens, so the cap must leave room for them.
            max_completion_tokens=1000,
            reasoning_effort="low",
        )
        result = json.loads(content)
        selected = {s.strip().lower() for s in result.get("skills", []) if isinstance(s, str)}
        if not selected:
            log.warning("Router returned no skills; falling back to all skills")
            return {s["name"] for s in selectable}
        log.info("Router selected skills: %s", selected)
        return selected
    except Exception:
        log.error("Skill router failed, falling back to all skills:\n%s", traceback.format_exc())
        return {s["name"] for s in selectable}
