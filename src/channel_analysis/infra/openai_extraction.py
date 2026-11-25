import json

from openai import OpenAIError

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import ExtractionError, ExtractionShapeError
from channel_analysis.core.models import ModelAnswer
from connectors import openai_client


class OpenAIExtractionModel:
    """The extraction model: a plain chat call in JSON mode. It is given no tools."""

    def __init__(self, model: str) -> None:
        self._model = model

    def complete_json(self, system: str, user: str, timeout_seconds: float) -> ModelAnswer:
        try:
            result = openai_client.chat_completion_with_usage(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=self._model,
                reasoning_effort="low",
                max_completion_tokens=ChannelAnalysisConfigs.EXTRACT_MAX_OUTPUT_TOKENS,
                json_object=True,
                timeout=timeout_seconds,
                # With the SDK's own retries the timeout would apply to each attempt, and a hung chunk would take three times as long.
                max_retries=0,
            )
        except OpenAIError as exc:
            raise ExtractionError(f"The extraction model failed ({exc.__class__.__name__}).") from exc
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise ExtractionShapeError("The answer was not JSON (it may have been cut off).", result.prompt_tokens, result.completion_tokens) from exc
        if not isinstance(data, dict):
            raise ExtractionShapeError("The answer was not a JSON object.", result.prompt_tokens, result.completion_tokens)
        return ModelAnswer(data, result.prompt_tokens, result.completion_tokens)
