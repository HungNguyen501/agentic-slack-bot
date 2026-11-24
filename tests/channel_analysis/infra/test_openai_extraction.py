from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

from channel_analysis.core import limits
from channel_analysis.core.errors import ExtractionError, ExtractionShapeError
from channel_analysis.infra.openai_extraction import OpenAIExtractionModel
from connectors.openai_client import ChatResult


@pytest.fixture
def chat():
    with patch("channel_analysis.infra.openai_extraction.openai_client", autospec=True) as client:
        client.chat_completion_with_usage.return_value = ChatResult('{"records": [{"m": 1}]}', 1200, 80)
        yield client.chat_completion_with_usage


def test_the_model_is_asked_in_json_mode_with_no_tools_a_timeout_and_the_configured_model(chat):
    answer = OpenAIExtractionModel("cheap-model").complete_json("SYSTEM", "USER", 90)

    chat.assert_called_once_with(
        [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}],
        model="cheap-model",
        reasoning_effort="low",
        max_completion_tokens=limits.EXTRACT_MAX_OUTPUT_TOKENS,
        json_object=True,
        timeout=90,
        max_retries=0,
    )
    assert (answer.data, answer.prompt_tokens, answer.completion_tokens) == ({"records": [{"m": 1}]}, 1200, 80)


@pytest.mark.parametrize("text", ["", "not json", '{"records": [', "[1, 2]", '"text"'], ids=["empty", "words", "cut-off", "list", "string"])
def test_an_answer_that_is_not_a_json_object_is_a_shape_error_so_the_chunk_is_asked_again(chat, text):
    chat.return_value = ChatResult(text, 10, 10)

    with pytest.raises(ExtractionShapeError):
        OpenAIExtractionModel("m").complete_json("s", "u", 90)


def test_a_failed_call_is_an_extraction_error_that_names_what_failed_and_not_the_request(chat):
    chat.side_effect = APITimeoutError(request=httpx.Request("POST", "https://api.openai.test/v1/chat/completions"))

    with pytest.raises(ExtractionError, match="APITimeoutError") as raised:
        OpenAIExtractionModel("m").complete_json("secret system prompt", "secret messages", 90)

    assert not isinstance(raised.value, ExtractionShapeError)
    assert "secret" not in str(raised.value)
