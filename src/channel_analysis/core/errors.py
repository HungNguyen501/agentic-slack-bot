from channel_analysis.core.configs import ChannelAnalysisConfigs


class ChannelAnalysisError(Exception):
    """A request the feature can't serve; the message is fit to show an admin."""


class ChannelAccessError(ChannelAnalysisError):
    """The bot or the requester may not read the channel; `code` is a key of ACCESS_MESSAGES."""

    def __init__(self, code: str, detail: str | None = None, retry_after: float | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        message = ChannelAnalysisConfigs.ACCESS_MESSAGES[code]
        super().__init__(f"{message} (Slack says it needs: {detail})" if detail else message)


class FileUnreadableError(ChannelAnalysisError):
    """A file that can't be turned into text (unsupported type, no text layer, damaged, too large)."""


class ExtractionError(ChannelAnalysisError):
    """The extraction model's call for one chunk failed (an error, a timeout); the chunk is reported as failed."""


class ExtractionShapeError(ExtractionError):
    """The model answered, but not with the expected JSON; the chunk is asked once more. The call was made and paid for, so
    what it used travels with the error."""

    def __init__(self, message: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
