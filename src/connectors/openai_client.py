"""OpenAI client — shared SDK instance plus thin wrappers over the Chat Completions and Responses APIs."""
from openai import OpenAI
from openai.types.responses import Response

from common.configs import Configs

client = OpenAI(api_key=Configs.OPENAI_API_KEY)


def chat_completion(
    messages: list[dict],
    *,
    model: str,
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
    json_object: bool = False,
) -> str:
    """Run a Chat Completions call and return the first choice's message text.

    Args:
        messages: Chat-format messages (role + content).
        model: Model id.
        reasoning_effort: Reasoning effort for reasoning models, or None to use the model default.
        max_completion_tokens: Cap on generated tokens. Reasoning tokens count against it, so
            leave room for them when reasoning is on.
        json_object: Request `response_format={"type": "json_object"}` so the reply is always parseable JSON.

    Returns:
        The message content of the first choice.
    """
    kwargs: dict = {}
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = max_completion_tokens
    if json_object:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    # content is None when a reasoning model spends the whole max_completion_tokens budget on reasoning.
    return response.choices[0].message.content or ""


def create_response(
    *,
    model: str,
    instructions: str,
    input: list[dict],
    tools: list[dict] | None = None,
    reasoning_effort: str | None = None,
) -> Response:
    """Run one Responses API turn with automatic tool choice.

    Args:
        model: Model id.
        instructions: System prompt for this turn.
        input: Conversation items — chat messages, plus the prior turns' function_call / reasoning /
            function_call_output items when continuing a tool-calling loop.
        tools: Function-calling tool schemas, or None for no tools.
        reasoning_effort: Reasoning effort for reasoning models, or None to use the model default.

    Returns:
        The raw SDK response, so callers can inspect `output` for function calls.
    """
    kwargs: dict = {}
    if tools:
        kwargs.update(tools=tools, tool_choice="auto")
    if reasoning_effort is not None:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    return client.responses.create(model=model, instructions=instructions, input=input, **kwargs)
