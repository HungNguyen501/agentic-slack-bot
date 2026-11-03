import json

from agent.access_control.tools import ACCESS_REQUEST_POSTED_MESSAGE, build_form_blocks, build_form_button_value
from common.configs import AgentConfigs


def test_build_form_button_value_derives_both_timestamps_from_now():
    value = json.loads(build_form_button_value("row_filter", "C1", "123.4", "U1", ["U2"], now=1_000))

    assert value == {
        "request_type": "row_filter",
        "channel": "C1",
        "thread_ts": "123.4",
        "requester_id": "U1",
        "reviewers": ["U2"],
        "created_at": 1_000,
        "expires_at": 1_000 + AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS,
    }


def test_build_form_blocks_shows_ttl_notice_and_carries_button_value():
    section, actions = build_form_blocks("payload")

    assert section["text"]["text"] == ACCESS_REQUEST_POSTED_MESSAGE
    assert actions["elements"][0]["action_id"] == "open_access_request_form"
    assert actions["elements"][0]["value"] == "payload"
