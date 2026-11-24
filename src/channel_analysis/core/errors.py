# What an admin is told for each way a channel can be unreadable. The codes are Slack's where Slack has one.
ACCESS_MESSAGES: dict[str, str] = {
    "channel_not_found": (
        "I can't find that channel. If it is private and I am not in it, it looks the same as a channel that does not exist: "
        "invite me, then send the channel link or ID."
    ),
    "channel_search_incomplete": (
        "This workspace has too many channels for me to find that one by name. Send me the channel link or its ID instead."
    ),
    "is_dm": "That is a direct message, and I only read channels.",
    "is_archived": "That channel is archived.",
    "not_in_channel": "I'm not a member of that channel, so I can't read it. Invite me with `/invite` and try again.",
    "missing_scope": (
        "My Slack app is missing a permission it needs to read channels (channels:history, groups:history, channels:read or groups:read). "
        "A workspace admin needs to add it and reinstall the app."
    ),
    "files_scope": "My Slack app is missing the files:read permission, so I can read messages but not files. A workspace admin needs to add it and reinstall the app.",
    "private_not_member": "That is a private channel and you are not a member of it, so I won't read it for you. Ask a member to run this, or join the channel first.",
    "membership_unverified": (
        "That is a private channel and I could not confirm you are a member of it (it has too many members to check), so I won't read it for you."
    ),
    "ratelimited": "Slack is rate limiting me. Try again in a minute.",
}


class ChannelAnalysisError(Exception):
    """A request the feature can't serve; the message is fit to show an admin."""


class ChannelAccessError(ChannelAnalysisError):
    """The bot or the requester may not read the channel; `code` is a key of ACCESS_MESSAGES."""

    def __init__(self, code: str, detail: str | None = None, retry_after: float | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        message = ACCESS_MESSAGES[code]
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
