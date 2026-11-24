from unittest.mock import MagicMock, create_autospec, patch

import pytest

from agent import agent
from agent.dispatch import ToolServices
from agent.skills.loader import SystemPrompt
from bots.core.models import BotConfig
from channel_analysis.core.service import ChannelAnalysisService
from characters.core.models import Character
from characters.core.service import CharacterService
from dataset_sync.core.service import DatasetSyncService
from schedules.core.ports import ScheduleRepository

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret", admin_users=frozenset({"UADMIN"}))
ANNA = Character(name="anna", display_name="Anna", bindings=("skill:core",), persona="Speak like Anna.")


@pytest.fixture
def characters():
    cast = create_autospec(CharacterService, instance=True)
    cast.character_for_skills.return_value = ANNA
    return cast


@pytest.fixture
def services(characters):
    return ToolServices(
        schedules=create_autospec(ScheduleRepository),
        dataset_sync=create_autospec(DatasetSyncService),
        characters=characters,
        channel_analysis=create_autospec(ChannelAnalysisService),
    )


@pytest.fixture
def saved_history():
    with patch("agent.agent.save_history", autospec=True) as save:
        yield save


@pytest.fixture
def llm(saved_history):
    with (
        patch("agent.agent.load_history", autospec=True, return_value=[]),
        patch("agent.agent.load_system_prompt", autospec=True, return_value=SystemPrompt("BASE PROMPT", ("metadata",), ("core",))),
        patch("agent.agent.openai_client", autospec=True) as openai,
    ):
        openai.create_response.return_value = MagicMock(output=[], output_text="the answer")
        yield openai


def run(services, **kwargs):
    return agent.run_agent("which tables?", "1.0", "C1", user_id="UADMIN", bot=BOT, services=services, **kwargs)


def test_the_character_is_picked_from_the_skills_the_router_chose(llm, services, characters):
    run(services)

    characters.character_for_skills.assert_called_once_with(("metadata",), ("core",), "C1", "1.0")


def test_the_characters_persona_follows_the_skills_in_the_instructions(llm, services):
    reply = run(services)

    instructions = llm.create_response.call_args.kwargs["instructions"]
    assert instructions.startswith("BASE PROMPT\n\n---\n\n## Voice\n")
    assert instructions.endswith("Speak like Anna.")
    assert reply == agent.AgentReply("the answer", ANNA)


def test_without_a_character_the_instructions_are_the_plain_prompt(llm, services, characters):
    characters.character_for_skills.return_value = None

    reply = run(services)

    assert llm.create_response.call_args.kwargs["instructions"] == "BASE PROMPT"
    assert reply == agent.AgentReply("the answer", None)


def test_the_history_keeps_the_clean_question_and_answer_whatever_the_voice(llm, saved_history, services):
    run(services)

    saved = saved_history.call_args.args[1]
    assert saved == [{"role": "user", "content": "which tables?"}, {"role": "assistant", "content": "the answer"}]


def test_the_processing_limit_message_still_reports_the_character(llm, services):
    call = MagicMock(type="function_call", arguments="{}", call_id="c1")
    call.name = "execute_query"
    call.model_dump.return_value = {}
    llm.create_response.return_value = MagicMock(output=[call], output_text="")

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="rows"):
        reply = run(services)

    assert reply.text.startswith("Sorry, I hit a processing limit")
    assert reply.character == ANNA


def test_run_agent_still_requires_a_bot(llm, services):
    with pytest.raises(ValueError, match="bot is required"):
        agent.run_agent("q", "1.0", "C1", services=services)


def test_the_summary_takes_the_same_voice_as_the_answer():
    with patch("agent.agent.openai_client", autospec=True) as openai:
        openai.chat_completion.return_value = " Short. "

        summary = agent.summarize_answer("q", "answer", ANNA)

    system = openai.chat_completion.call_args.args[0][0]["content"]
    assert summary == "Short."
    assert system.endswith("Speak like Anna.") and "## Voice" in system


def test_the_summary_without_a_character_is_unchanged():
    with patch("agent.agent.openai_client", autospec=True) as openai:
        openai.chat_completion.return_value = "Short."

        agent.summarize_answer("q", "answer")

    assert "## Voice" not in openai.chat_completion.call_args.args[0][0]["content"]


def tool_call(name, call_id="c1", arguments="{}"):
    call = MagicMock(type="function_call", arguments=arguments, call_id=call_id)
    call.name = name
    call.model_dump.return_value = {"type": "function_call", "name": name, "call_id": call_id}
    return call


def test_before_any_channel_read_every_tool_is_offered_and_after_it_only_the_read_only_ones(llm, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("read_channel_messages")], output_text=""),
        MagicMock(output=[], output_text="the answer"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="page") as dispatch_tool:
        reply = run(services)

    first, second = (c.kwargs["tools"] for c in llm.create_response.call_args_list)
    assert "remove_schedule" in {t["name"] for t in first}
    assert {t["name"] for t in second} == agent.READ_ONLY_TOOLS
    assert dispatch_tool.call_count == 1
    assert reply.text == "the answer"


def test_a_write_tool_called_after_a_channel_read_is_refused_without_running(llm, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("read_channel_file")], output_text=""),
        MagicMock(output=[tool_call("remove_schedule", "c2", '{"id": "s1"}')], output_text=""),
        MagicMock(output=[], output_text="the answer"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="page") as dispatch_tool:
        run(services)

    assert [c.args[0] for c in dispatch_tool.call_args_list] == ["read_channel_file"]
    outputs = [m for m in llm.create_response.call_args_list[2].kwargs["input"] if m.get("type") == "function_call_output"]
    assert outputs[-1]["output"].startswith("Refused: channel or file content was read")


def test_a_read_only_tool_still_runs_after_a_channel_read(llm, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("read_channel_messages")], output_text=""),
        MagicMock(output=[tool_call("execute_query", "c2", '{"sql": "select 1"}')], output_text=""),
        MagicMock(output=[], output_text="the answer"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="rows") as dispatch_tool:
        run(services)

    assert [c.args[0] for c in dispatch_tool.call_args_list] == ["read_channel_messages", "execute_query"]


def test_a_check_alone_does_not_narrow_the_tools(llm, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("check_channel_access")], output_text=""),
        MagicMock(output=[], output_text="the answer"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="OK"):
        run(services)

    assert "remove_schedule" in {t["name"] for t in llm.create_response.call_args_list[1].kwargs["tools"]}


def test_the_unattended_categories_reach_the_tool_dispatch_and_the_auth_note(llm, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("read_channel_messages")], output_text=""),
        MagicMock(output=[], output_text="the answer"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="page") as dispatch_tool:
        agent.run_agent("analyze", "1.0", "C1", bot=BOT, services=services, unattended_categories=frozenset({"channel_analysis"}))

    assert dispatch_tool.call_args.args[2] is None
    assert dispatch_tool.call_args.args[-1] == frozenset({"channel_analysis"})
    note = next(m["content"] for m in llm.create_response.call_args_list[0].kwargs["input"] if m.get("role") == "user")
    assert note.startswith("[Scheduled run, no requester — authorized only for: channel_analysis]")


def test_an_answer_built_from_channel_content_is_marked_as_data_in_the_saved_history(llm, saved_history, services):
    llm.create_response.side_effect = [
        MagicMock(output=[tool_call("read_channel_messages")], output_text=""),
        MagicMock(output=[], output_text="11 of 34 reports were empty"),
    ]

    with patch("agent.agent.dispatch_tool", autospec=True, return_value="page"):
        reply = run(services)

    saved = saved_history.call_args.args[1]
    assert reply.text == "11 of 34 reports were empty"
    assert saved[-1] == {"role": "assistant", "content": agent.UNTRUSTED_ANSWER_MARK + "11 of 34 reports were empty"}
    assert saved[-2] == {"role": "user", "content": "which tables?"}


def test_an_answer_that_never_touched_channel_content_is_saved_as_it_is(llm, saved_history, services):
    run(services)

    assert saved_history.call_args.args[1][-1] == {"role": "assistant", "content": "the answer"}
