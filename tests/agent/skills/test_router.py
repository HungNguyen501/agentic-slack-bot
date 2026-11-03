import json
from unittest.mock import patch

import pytest

from agent.skills.router import _parse_selection, select_skills

SELECTABLE = [
    {"name": "metadata", "description": "catalogs and tables"},
    {"name": "jobs", "description": "job runs"},
]
ALL_NAMES = {"metadata", "jobs"}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"skills": ["Metadata "]}, {"metadata"}),
        ({"skills": ["jobs", "unknown"]}, {"jobs"}),
        ({"skills": ["jobs", 3, None]}, {"jobs"}),
    ],
    ids=["normalizes-case-and-space", "drops-unknown-names", "drops-non-strings"],
)
def test_parse_selection_keeps_only_available_names(reply, expected):
    assert _parse_selection(json.dumps(reply), ALL_NAMES) == expected


@pytest.mark.parametrize(
    "reply",
    [{"skills": "metadata"}, {"skills": ["unknown"]}, {"skills": []}, {"other": 1}, ["metadata"]],
    ids=["string-not-list", "only-unknown", "empty", "missing-key", "not-an-object"],
)
def test_parse_selection_returns_empty_for_unusable_reply(reply):
    assert _parse_selection(json.dumps(reply), ALL_NAMES) == set()


@pytest.mark.parametrize(
    "reply",
    [{"skills": "metadata"}, {"skills": ["unknown"]}],
    ids=["string-not-list", "only-unknown"],
)
def test_select_skills_falls_back_to_all_when_reply_names_no_valid_skill(reply):
    with patch("agent.skills.router.openai_client.chat_completion", autospec=True, return_value=json.dumps(reply)):
        assert select_skills("q", SELECTABLE) == ALL_NAMES


def test_select_skills_falls_back_to_all_when_router_call_fails():
    with patch("agent.skills.router.openai_client.chat_completion", autospec=True, side_effect=RuntimeError("boom")):
        assert select_skills("q", SELECTABLE) == ALL_NAMES


def test_select_skills_returns_the_routed_subset():
    with patch("agent.skills.router.openai_client.chat_completion", autospec=True, return_value='{"skills": ["jobs"]}'):
        assert select_skills("q", SELECTABLE) == {"jobs"}


def test_select_skills_returns_empty_without_calling_router_when_nothing_selectable():
    with patch("agent.skills.router.openai_client.chat_completion", autospec=True) as router:
        assert select_skills("q", []) == set()

    router.assert_not_called()
