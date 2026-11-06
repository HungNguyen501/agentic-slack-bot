import json
from unittest.mock import patch

from agent.access_control import tools
from agent.access_control.access_request_categories import AccessRequestCategory
from agent.access_control.tools import ACCESS_REQUEST_POSTED_MESSAGE, build_form_blocks, build_form_button_value
from agent.dataset_sync.store import DatasetNotSyncedError
from agent.forms.access_request_options import LOCATIONS_DATASET, TAGS_DATASET, FormOptions
from common.configs import AgentConfigs
from connectors.db.bots import BotConfig

BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")
LOCATIONS = [{"company_key": "eaze_com", "company": "Eaze", "location_market": "CA", "location": "Eaze"}]
TAGS = [{"tag_name": "transaction"}]


def test_build_form_button_value_derives_both_timestamps_from_now():
    value = json.loads(build_form_button_value("row_filter", "C1", "123.4", "U1", ["U2"], "tok", now=1_000))

    assert value == {
        "request_type": "row_filter",
        "channel": "C1",
        "thread_ts": "123.4",
        "requester_id": "U1",
        "reviewers": ["U2"],
        "options_token": "tok",
        "created_at": 1_000,
        "expires_at": 1_000 + AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS,
    }


def test_build_form_blocks_shows_ttl_notice_and_carries_button_value():
    section, actions = build_form_blocks("payload")

    assert section["text"]["text"] == ACCESS_REQUEST_POSTED_MESSAGE
    assert actions["elements"][0]["action_id"] == "open_access_request_form"
    assert actions["elements"][0]["value"] == "payload"


def rows_by_dataset(name):
    return {LOCATIONS_DATASET: LOCATIONS, TAGS_DATASET: TAGS}[name]


def test_stash_form_options_writes_the_snapshot_to_redis_for_the_button_lifetime():
    with (
        patch("agent.access_control.tools.store.get_rows", autospec=True, side_effect=rows_by_dataset),
        patch("agent.access_control.tools.redis_client", autospec=True) as redis_client,
    ):
        token = tools.stash_form_options()

    key, payload = redis_client.set.call_args.args
    assert key == f"access_form_options:{token}"
    assert FormOptions.from_json(payload).tags == ["transaction"]
    assert redis_client.set.call_args.kwargs == {"ex": AgentConfigs.ACCESS_REQUEST_BUTTON_TTL_SECONDS}


def test_dispatch_refuses_without_posting_when_a_dataset_was_never_synced():
    category = AccessRequestCategory(id="c1", bot_id="bot-a", request_type="r", channel_ids=["C1"], reviewers=["U2"])
    with (
        patch("agent.access_control.tools.get_access_request_category", autospec=True, return_value=category),
        patch("agent.access_control.tools.store.get_rows", autospec=True, side_effect=DatasetNotSyncedError("Dataset 'store_locations' has not been synced yet.")),
        patch("agent.access_control.tools.slack.post_message", autospec=True) as post,
    ):
        reply = tools.dispatch_data_access_tool({"request_type": "r"}, "U1", "C1", "1.0", BOT)

    assert "has not been synced yet" in reply
    post.assert_not_called()
