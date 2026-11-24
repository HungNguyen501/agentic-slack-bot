from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from connectors import openai_client


def completion(content="hello", usage=(12, 3)):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None,
    )


def test_the_text_comes_back_with_the_tokens_the_call_used():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.chat.completions.create.return_value = completion("hi", (100, 7))

        result = openai_client.chat_completion_with_usage([{"role": "user", "content": "x"}], model="m")

    assert result == openai_client.ChatResult("hi", 100, 7)


def test_every_option_is_passed_on_and_an_unset_one_is_left_out():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.chat.completions.create.return_value = completion()

        openai_client.chat_completion_with_usage([], model="m")
        bare = client.chat.completions.create.call_args.kwargs
        openai_client.chat_completion_with_usage([], model="m", reasoning_effort="low", max_completion_tokens=50, json_object=True, timeout=9)
        full = client.chat.completions.create.call_args.kwargs

    assert bare == {"model": "m", "messages": []}
    assert full == {
        "model": "m", "messages": [], "reasoning_effort": "low", "max_completion_tokens": 50, "response_format": {"type": "json_object"}, "timeout": 9,
    }  # fmt: skip


def test_an_empty_reasoning_answer_and_missing_usage_are_zeros_not_errors():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.chat.completions.create.return_value = completion(None, usage=None)

        assert openai_client.chat_completion_with_usage([], model="m") == openai_client.ChatResult("", 0, 0)


def test_chat_completion_still_returns_just_the_text():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.chat.completions.create.return_value = completion("just text")

        assert openai_client.chat_completion([], model="m", json_object=True) == "just text"

    assert client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_a_call_that_asks_for_no_retries_goes_through_a_client_with_none():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.with_options.return_value.chat.completions.create.return_value = completion("hi")

        openai_client.chat_completion_with_usage([], model="m", timeout=90, max_retries=0)

    client.with_options.assert_called_once_with(max_retries=0)
    client.chat.completions.create.assert_not_called()
    assert client.with_options.return_value.chat.completions.create.call_args.kwargs["timeout"] == 90


def test_a_call_that_does_not_ask_keeps_the_clients_default_retries():
    with patch.object(openai_client, "client", MagicMock()) as client:
        client.chat.completions.create.return_value = completion("hi")

        openai_client.chat_completion_with_usage([], model="m")

    client.with_options.assert_not_called()
