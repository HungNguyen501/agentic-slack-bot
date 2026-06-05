"""Databricks assistant agent powered by OpenAI function calling."""
import json
import logging
import os
import traceback
from datetime import date, timedelta

from openai import OpenAI
from redis import Redis

from connectors import databricks, postgres
from connectors.bots import BotConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker.agent")

GPT_MODEL = "gpt-5.5-2026-04-23"
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "gpt-4o-mini")
_SCHEDULE_TOOLS = {"list_schedules", "add_schedule", "update_schedule", "remove_schedule"}

openai_client = OpenAI(api_key=OPENAI_API_KEY)
redis_client = Redis.from_url(REDIS_URL)


def _parse_skill_file(filename: str) -> dict:
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


def _load_all_skills() -> list[dict]:
    """Load and sort all skill files from the skills directory.

    Returns:
        List of parsed skill dicts sorted by filename, each produced by _parse_skill_file.
    """
    return [
        _parse_skill_file(f)
        for f in sorted(f for f in os.listdir(SKILLS_DIR) if f.endswith(".md"))
    ]


def _select_skills(question: str, selectable: list[dict], history: list[dict] | None = None) -> set[str]:
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
        response = openai_client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[
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
            response_format={"type": "json_object"},
            max_tokens=100,
        )
        result = json.loads(response.choices[0].message.content)
        selected = {s.strip().lower() for s in result.get("skills", []) if isinstance(s, str)}
        if not selected:
            log.warning("Router returned no skills; falling back to all skills")
            return {s["name"] for s in selectable}
        log.info("Router selected skills: %s", selected)
        return selected
    except Exception:
        log.error("Skill router failed, falling back to all skills:\n%s", traceback.format_exc())
        return {s["name"] for s in selectable}


def _load_system_prompt(
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

    all_skills = _load_all_skills()
    always_skills = [s for s in all_skills if s["always"]]
    selectable = [s for s in all_skills if not s["always"]]

    if enabled_skills:
        selectable = [s for s in selectable if s["name"] in enabled_skills]

    selected_names = _select_skills(question, selectable, history)
    active = always_skills + [s for s in selectable if s["name"].lower() in selected_names]

    base = "\n\n---\n\n".join(s["body"] for s in active)
    return (
        f"Today's date: {today.isoformat()}\n"
        f"180-day window cutoff: {cutoff.isoformat()} — any date on or after this is within the allowed window.\n\n"
        f"{base}"
    )


def _load_history(thread_ts: str) -> list[dict]:
    """Retrieve stored conversation history for a Slack thread from Redis.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.

    Returns:
        List of message dicts in OpenAI chat format (role + content), or an empty list if
        no history exists for the thread.
    """
    raw = redis_client.get(f"chat_history:{thread_ts}")
    return json.loads(raw) if raw else []


def _save_history(thread_ts: str, history: list[dict]) -> None:
    """Trim the conversation history to the last 20 pairs and persist it to Redis with a 24-hour TTL.

    Args:
        thread_ts: Slack thread timestamp used as the Redis cache key.
        history: Full list of user/assistant message dicts accumulated during the agent loop.
    """
    history_ttl = 86400  # 24 hours
    max_history_turns = 20  # keep last 20 user/assistant pairs
    max_messages = max_history_turns * 2

    if len(history) > max_messages:
        history = history[-max_messages:]

    redis_client.set(f"chat_history:{thread_ts}", json.dumps(history), ex=history_ttl)


def _get_agent_tools() -> list[dict]:
    """Return the OpenAI function-calling tool schema for all agent tools.

    Returns:
        List of tool dicts in the OpenAI tools format, covering execute_query and all
        schedule management tools (list, add, update, remove).
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "execute_query",
                "description": (
                    "Execute a SQL SELECT query against Databricks system tables. "
                    "Use this to answer questions about catalogs, schemas, tables, columns, "
                    "jobs, job run history, or data lineage."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "A SQL SELECT statement targeting Databricks system tables."},
                    },
                    "required": ["sql"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_job_run_details",
                "description": (
                    "Fetch the actual error message and per-task failure details for a specific "
                    "Databricks job run via the Jobs REST API. Use this after identifying a failed "
                    "run_id from execute_query to get the human-readable error text for investigation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "description": "The job run ID from job_run_timeline.run_id"},
                    },
                    "required": ["run_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_schedules",
                "description": "List all active scheduled questions.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_schedule",
                "description": "Create a new scheduled question posted to a Slack channel on a cron.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cron": {"type": "string", "description": "Cron expression (5 fields, UTC). E.g. '0 9 * * 1-5'"},
                        "channel": {"type": "string", "description": "Slack channel ID, e.g. C1234567890"},
                        "question": {"type": "string", "description": "The question text to send on schedule"},
                    },
                    "required": ["cron", "channel", "question"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_schedule",
                "description": "Update one or more fields of an existing schedule by its UUID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Schedule UUID"},
                        "cron": {"type": "string"},
                        "channel": {"type": "string"},
                        "question": {"type": "string"},
                    },
                    "required": ["id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "remove_schedule",
                "description": "Permanently delete a scheduled question by its UUID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Schedule UUID to remove"},
                    },
                    "required": ["id"],
                },
            },
        },
    ]


def _dispatch_schedule_tool(name: str, args: dict, bot_id: str) -> str:
    """Execute a schedule management tool and return a human-readable result string.

    Args:
        name: Tool name — one of list_schedules, add_schedule, update_schedule, remove_schedule.
        args: Parsed JSON arguments from the LLM tool call.

    Returns:
        A plain-text or Slack-mrkdwn confirmation string, or an error message if the
        operation fails or the tool name is unrecognised.
    """
    try:
        if name == "list_schedules":
            schedules = postgres.get_schedules()
            if not schedules:
                return "No schedules configured."
            header = f"{'ID':<36}  {'Cron':<20}  {'Channel':<12}  Question"
            divider = "-" * len(header)
            rows = [header, divider] + [
                f"{s['id']:<36}  {s['cron']:<20}  {s['channel']:<12}  {s['question']}"
                for s in schedules
            ]
            return f"```\n{chr(10).join(rows)}\n```"

        if name == "add_schedule":
            s = postgres.add_schedule(args["cron"], args["channel"], args["question"], bot_id=bot_id)
            return f"Schedule created — id: `{s['id']}`"

        if name == "update_schedule":
            s = postgres.update_schedule(
                args["id"],
                cron=args.get("cron"),
                channel=args.get("channel"),
                question=args.get("question"),
            )
            if not s:
                return f"Schedule `{args['id']}` not found or no fields to update."
            return f"Schedule `{s['id']}` updated."

        if name == "remove_schedule":
            deleted = postgres.remove_schedule(args["id"])
            if deleted:
                return f"Schedule `{args['id']}` removed."
            return f"Schedule `{args['id']}` not found."

    except Exception as exc:
        log.exception("Schedule tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown schedule tool: {name}"


def _dispatch_tool(name: str, args: dict, user_id: str | None, admin_users: frozenset[str], bot_id: str) -> str:
    """Route a single tool call to the correct handler, enforcing the per-bot admin whitelist.

    Args:
        name: Tool name as returned by the LLM.
        args: Parsed JSON arguments from the LLM tool call.
        user_id: Slack user ID of the requester.
        admin_users: Per-bot set of Slack user IDs allowed to manage schedules.

    Returns:
        String result to pass back to the LLM as the tool response.
    """
    if name == "execute_query":
        sql = args.get("sql", "")
        log.info("Databricks query: %s", sql)
        return databricks.run_query(sql)

    if name == "get_job_run_details":
        run_id = args.get("run_id", "")
        log.info("Fetching job run details for run_id=%s", run_id)
        return databricks.get_job_run(run_id)

    if name in _SCHEDULE_TOOLS:
        if user_id not in admin_users:
            return "You are not authorized to manage schedules."
        return _dispatch_schedule_tool(name, args, bot_id)

    return f"Unknown tool: {name}"


def run_agent(
    question: str,
    thread_ts: str,
    user_id: str | None = None,
    bot: BotConfig | None = None,
) -> str:
    """Run the OpenAI agentic loop with per-thread conversation history.

    Args:
        question: User's question with the @mention prefix already stripped.
        thread_ts: Slack thread timestamp used to scope conversation history.
        user_id: Slack user ID of the requester; used for schedule tool authorization.
        bot: Per-bot config carrying admin_users and enabled_skills.

    Returns:
        Agent's final answer as a Slack-formatted string.
    """
    if bot is None:
        raise ValueError("bot is required — must be loaded from the bots registry")

    admin_users = bot.admin_users
    enabled_skills = bot.enabled_skills

    history = _load_history(thread_ts)
    log.info("Loaded %d history messages for thread %s", len(history), thread_ts)

    messages: list[dict] = [
        {"role": "system", "content": _load_system_prompt(question, history, enabled_skills)}
    ]
    messages.extend(history)

    # Prepend the current user's authorization status so the LLM re-evaluates
    # permissions for this request rather than echoing a prior refusal from history.
    is_authorized = user_id in admin_users
    auth_note = (
        f"[Current requester: {user_id or 'unknown'} — "
        + ("authorized for schedule management]" if is_authorized else "NOT authorized for schedule management]")
    )
    messages.append({"role": "user", "content": f"{auth_note}\n{question}"})

    for _ in range(10):
        response = openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=messages,
            tools=_get_agent_tools(),
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            answer = msg.content or "I wasn't able to generate a response."
            history.append({"role": "user", "content": question})  # store clean question, not the auth-annotated one
            history.append({"role": "assistant", "content": answer})
            _save_history(thread_ts, history)
            return answer

        messages.append(msg.model_dump())

        for tool_call in msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            result = _dispatch_tool(tool_call.function.name, args, user_id, admin_users, bot.bot_id)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

    return "Sorry, I hit a processing limit. Please try a more specific question."
