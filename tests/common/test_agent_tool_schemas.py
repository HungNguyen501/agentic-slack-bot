import pytest

from common.configs import AgentConfigs

TOOLS = {tool.name: tool for tool in AgentConfigs.TOOLS}
WINDOW = ("lookback_days", "end_days_ago", "contains", "include_replies", "before")


@pytest.mark.parametrize("name", ["read_channel_messages", "analyze_channel"])
def test_the_channel_tools_that_pick_a_window_take_the_same_window_parameters(name):
    properties = TOOLS[name].parameters["properties"]

    assert all(key in properties for key in WINDOW)
    assert properties["lookback_days"] == TOOLS["read_channel_messages"].parameters["properties"]["lookback_days"]
    assert properties["end_days_ago"] == TOOLS["read_channel_messages"].parameters["properties"]["end_days_ago"]
    assert properties["before"] == TOOLS["read_channel_messages"].parameters["properties"]["before"]


def test_each_tool_gets_its_own_copy_so_changing_one_schema_cannot_change_the_other():
    read, analyze = (TOOLS[n].parameters["properties"] for n in ("read_channel_messages", "analyze_channel"))

    assert read["lookback_days"] is not analyze["lookback_days"]
    assert read["contains"]["items"] is not analyze["contains"]["items"]


def test_a_tool_can_word_a_shared_parameter_for_itself():
    read, analyze = (TOOLS[n].parameters["properties"] for n in ("read_channel_messages", "analyze_channel"))

    assert "kept" in read["contains"]["description"] and "analyzed" in analyze["contains"]["description"]
    assert read["contains"]["type"] == analyze["contains"]["type"] == "array"
    assert "faster" in read["include_replies"]["description"] and "faster" not in analyze["include_replies"]["description"]


def test_analyze_channel_requires_what_it_cannot_guess():
    assert TOOLS["analyze_channel"].parameters["required"] == ["channel", "instruction", "fields"]
    assert TOOLS["read_channel_messages"].parameters["required"] == ["channel"]
