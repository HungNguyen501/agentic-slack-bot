"""Databricks assistant agent powered by OpenAI function calling."""
import json
import logging
import traceback

from agent.access_control.tools import ACCESS_REQUEST_POSTED_MESSAGE
from agent.dispatch import dispatch_tool, get_agent_tools, tool_category
from agent.history import load_history, save_history
from agent.skills.loader import load_system_prompt
from common.configs import AgentConfigs, Configs
from connectors import openai_client
from connectors.db.bots import BotConfig

log = logging.getLogger("agent")


def summarize_answer(question: str, answer: str) -> str:
    """Condense a full agent answer into a short Slack-friendly summary.

    Args:
        question: The original question that produced the answer.
        answer: The agent's full answer text.

    Returns:
        A 1-3 sentence summary in Slack mrkdwn, or the original answer if summarization fails.
    """
    try:
        summary = openai_client.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize the following answer to a Databricks data question into 1-2 short "
                        "sentences suitable as a Slack message preview — a teaser, not the report itself. "
                        "The full detailed answer is posted right below in the same thread, so give only "
                        "the high-level gist (e.g. overall counts or status) and never repeat detail that "
                        "belongs in the full answer: no bullet lists, no per-item breakdowns, no links. "
                        "Write it in a funny, friendly, easy-to-understand tone — like a helpful teammate, "
                        "not a report. Light humor and a touch of personality (an emoji or two is fine) "
                        "are welcome, but never at the expense of accuracy: any number you do mention must "
                        "be exact. React with genuine feeling to the result: if everything looks healthy "
                        "(e.g. zero failures), celebrate it warmly (\"Great day, no failed jobs!\"); if "
                        "something's off, show a bit of empathy or mild concern rather than staying flat "
                        "and neutral. Use Slack mrkdwn, no headers."
                    ),
                },
                {"role": "user", "content": f"Question: {question}\n\nAnswer:\n{answer}"},
            ],
            model=Configs.ROUTER_MODEL,
            max_completion_tokens=1000,
            reasoning_effort="low",
        ).strip()
        return summary or answer
    except Exception:
        log.error("Summarization failed, falling back to full answer:\n%s", traceback.format_exc())
        return answer


def run_agent(
    question: str,
    thread_ts: str,
    channel: str,
    user_id: str | None = None,
    bot: BotConfig | None = None,
) -> str:
    """Run the OpenAI agentic loop with per-thread conversation history.

    Args:
        question: User's question with the @mention prefix already stripped.
        thread_ts: Slack thread timestamp used to scope conversation history.
        channel: Slack channel ID the question was asked in; needed by tools that post
            interactive messages (e.g. request_data_access) into the same thread.
        user_id: Slack user ID of the requester; used for schedule tool authorization.
        bot: Per-bot config carrying admin_users and enabled_skills.

    Returns:
        Agent's final answer as a Slack-formatted string.
    """
    if bot is None:
        raise ValueError("bot is required — must be loaded from the bots registry")

    admin_users = bot.admin_users
    enabled_skills = bot.enabled_skills

    history = load_history(thread_ts)
    log.info("Loaded %d history messages for thread %s", len(history), thread_ts)

    instructions = load_system_prompt(question, history, enabled_skills)
    messages: list[dict] = list(history)

    # Prepend the current user's authorization status so the LLM re-evaluates
    # permissions for this request rather than echoing a prior refusal from history.
    is_authorized = user_id in admin_users
    auth_note = (
        f"[Current requester: {user_id or 'unknown'} — "
        + ("authorized for schedule management]" if is_authorized else "NOT authorized for schedule management]")
    )
    messages.append({"role": "user", "content": f"{auth_note}\n{question}"})

    for _ in range(10):
        response = openai_client.create_response(
            model=AgentConfigs.GPT_MODEL,
            instructions=instructions,
            input=messages,
            tools=get_agent_tools(),
            reasoning_effort=AgentConfigs.REASONING_EFFORT,
        )
        tool_calls = [item for item in response.output if item.type == "function_call"]

        if not tool_calls:
            answer = response.output_text or "I wasn't able to generate a response."
            history.append({"role": "user", "content": question})  # store clean question, not the auth-annotated one
            history.append({"role": "assistant", "content": answer})
            save_history(thread_ts, history)
            return answer

        # Reasoning items must be echoed back alongside their function_call items on the next turn.
        messages.extend(item.model_dump(exclude_none=True) for item in response.output)

        for tool_call in tool_calls:
            args = json.loads(tool_call.arguments)
            result = dispatch_tool(tool_call.name, args, user_id, admin_users, bot, channel, thread_ts)
            messages.append({"type": "function_call_output", "call_id": tool_call.call_id, "output": result})

            if tool_category(tool_call.name) == "data_access" and result == ACCESS_REQUEST_POSTED_MESSAGE:
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": result})
                save_history(thread_ts, history)
                return ""

    return "Sorry, I hit a processing limit. Please try a more specific question."
