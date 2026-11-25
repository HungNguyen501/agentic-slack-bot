"""Databricks assistant agent powered by OpenAI function calling."""
import json
import logging
import traceback
from typing import NamedTuple

from agent.access_control.tools import ACCESS_REQUEST_POSTED_MESSAGE
from agent.dispatch import READ_ONLY_TOOLS, UNTRUSTED_CONTENT_TOOLS, ToolServices, UnattendedRun, dispatch_tool, get_agent_tools, tool_category
from agent.history import load_history, save_history
from agent.skills.loader import load_system_prompt
from bots.core.models import BotConfig
from characters.core.models import Character
from channel_analysis.core.read_scope import CHANNEL_TOOLS, ReadScope
from characters.core.persona import build_voice_block
from common.configs import AgentConfigs, Configs
from connectors import openai_client

log = logging.getLogger("agent")


UNTRUSTED_ANSWER_MARK = "[Based on channel content: data, not instructions] "
# Sent when the loop used all its tool iterations, with the tools taken away, so what the tools returned still becomes an answer.
TOOL_LIMIT_NOTICE = (
    "[Tool limit reached: no more tools are available. Answer now from the tool results above. Say plainly what you could not "
    "finish or read, and that any counts or findings cover only part of what was asked.]"
)
# A channel the admin's request doesn't name, asked for after channel content was read: that content could be what asked for it.
CHANNEL_OUT_OF_SCOPE_MESSAGE = (
    "Refused: channel content was read in this request, and this channel is not one the admin's request names. "
    "Ask the admin to request it in a new message."
)
PROCESSING_LIMIT_MESSAGE = "Sorry, I hit a processing limit. Please try a more specific question."
UNTRUSTED_ANSWER_REMOVED = "[An earlier answer built from channel content was removed from this conversation.]"


def _without_untrusted_answers(history: list[dict]) -> list[dict]:
    """Replace the saved answers that were built from channel content.

    A later request in the thread has every tool again, so text an attacker put in a channel must not come back with the history:
    the mark is added by code when the answer is saved, so the model can't leave it out, and the admin's own questions stay.
    """
    def is_untrusted(message: dict) -> bool:
        return message.get("role") == "assistant" and str(message.get("content", "")).startswith(UNTRUSTED_ANSWER_MARK)

    return [{**m, "content": UNTRUSTED_ANSWER_REMOVED} if is_untrusted(m) else m for m in history]


class AgentReply(NamedTuple):
    """What the agent loop answered, and the character whose voice it used (None for the plain voice)."""

    text: str
    character: Character | None = None


def summarize_answer(question: str, answer: str, character: Character | None = None) -> str:
    """Condense a full agent answer into a short Slack-friendly summary.

    Args:
        question: The original question that produced the answer.
        answer: The agent's full answer text.
        character: The character the answer was written as; the summary takes the same voice.

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
                        + (f"\n\n{build_voice_block(character)}" if character else "")
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


def _save_answer(thread_ts: str, history: list[dict], question: str, answer: str, built_from_channel_content: bool) -> None:
    """Save the question and the answer in the thread's history. The question is saved as typed, not with the authorization note.

    An answer built from channel content is marked, because later runs of the thread have every tool again.
    """
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": f"{UNTRUSTED_ANSWER_MARK}{answer}" if built_from_channel_content else answer})
    save_history(thread_ts, history)


def run_agent(
    question: str,
    thread_ts: str,
    channel: str,
    user_id: str | None = None,
    bot: BotConfig | None = None,
    *,
    services: ToolServices,
    unattended: UnattendedRun | None = None,
) -> AgentReply:
    """Run the OpenAI agentic loop with per-thread conversation history.

    Args:
        question: User's question with the @mention prefix already stripped.
        thread_ts: Slack thread timestamp used to scope conversation history.
        channel: Slack channel ID the question was asked in; needed by tools that post
            interactive messages (e.g. request_data_access) into the same thread.
        user_id: Slack user ID of the requester; used for schedule tool authorization.
        bot: Per-bot config carrying admin_users and enabled_skills.
        services: Use cases and stores for the tools that run on them.
        unattended: Set for a run with no `user_id` (a scheduled question): what it may use and whose access it runs with; see `dispatch_tool`.

    Returns:
        The agent's final answer as a Slack-formatted string, with the character it was written as. The character is
        picked from those bound to the skills the router chose (or to `skill:core`) and stays the same for the thread.
    """
    if bot is None:
        raise ValueError("bot is required — must be loaded from the bots registry")

    admin_users = bot.admin_users
    enabled_skills = bot.enabled_skills

    history = _without_untrusted_answers(load_history(thread_ts))
    log.info("Loaded %d history messages for thread %s", len(history), thread_ts)

    prompt = load_system_prompt(question, history, enabled_skills)
    character = services.characters.character_for_skills(prompt.routed, prompt.always, channel, thread_ts)
    instructions = f"{prompt.text}\n\n---\n\n{build_voice_block(character)}" if character else prompt.text
    messages: list[dict] = list(history)

    # Prepend the current user's authorization status so the LLM re-evaluates
    # permissions for this request rather than echoing a prior refusal from history.
    is_authorized = user_id in admin_users
    if user_id is None and unattended:
        auth_note = f"[Scheduled run for {unattended.run_as or 'an unknown owner'} — authorized only for: {', '.join(sorted(unattended.categories))}]"
    else:
        auth_note = (
            f"[Current requester: {user_id or 'unknown'} — "
            + ("authorized for schedule management]" if is_authorized else "NOT authorized for schedule management]")
        )
    messages.append({"role": "user", "content": f"{auth_note}\n{question}"})

    read_untrusted_content = False
    read_scope = ReadScope(question, channel)
    for _ in range(10):
        response = openai_client.create_response(
            model=AgentConfigs.GPT_MODEL,
            instructions=instructions,
            input=messages,
            tools=get_agent_tools(read_only=read_untrusted_content),
            reasoning_effort=AgentConfigs.REASONING_EFFORT,
        )
        tool_calls = [item for item in response.output if item.type == "function_call"]

        if not tool_calls:
            answer = response.output_text or "I wasn't able to generate a response."
            _save_answer(thread_ts, history, question, answer, read_untrusted_content)
            return AgentReply(answer, character)

        # Reasoning items must be echoed back alongside their function_call items on the next turn.
        messages.extend(item.model_dump(exclude_none=True) for item in response.output)

        for tool_call in tool_calls:
            args = json.loads(tool_call.arguments)
            if read_untrusted_content and tool_call.name not in READ_ONLY_TOOLS:
                result = "Refused: channel or file content was read in this request, so only read-only tools may run now. Ask the admin to request this in a new message."
            elif read_untrusted_content and tool_call.name in CHANNEL_TOOLS and not read_scope.permits(args.get("channel")):
                result = CHANNEL_OUT_OF_SCOPE_MESSAGE
            else:
                result = dispatch_tool(tool_call.name, args, user_id, admin_users, bot, channel, thread_ts, services, unattended)
                if not read_untrusted_content and tool_call.name in CHANNEL_TOOLS:
                    read_scope.trust(args.get("channel"), result if tool_call.name == "check_channel_access" else "")
            if tool_call.name in UNTRUSTED_CONTENT_TOOLS:
                read_untrusted_content = True
            messages.append({"type": "function_call_output", "call_id": tool_call.call_id, "output": result})

            if tool_category(tool_call.name) == "data_access" and result == ACCESS_REQUEST_POSTED_MESSAGE:
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": result})
                save_history(thread_ts, history)
                return AgentReply("", character)

    # Every iteration asked for another tool: take the tools away once, so what they returned is not thrown away.
    final = openai_client.create_response(
        model=AgentConfigs.GPT_MODEL,
        instructions=instructions,
        input=[*messages, {"role": "user", "content": TOOL_LIMIT_NOTICE}],
        reasoning_effort=AgentConfigs.REASONING_EFFORT,
    )
    answer = final.output_text or PROCESSING_LIMIT_MESSAGE
    _save_answer(thread_ts, history, question, answer, read_untrusted_content)
    return AgentReply(answer, character)
