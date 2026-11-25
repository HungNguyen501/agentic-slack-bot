from typing import Protocol

from channel_analysis.core.models import ChannelInfo, FileMeta, HistoryBatch, ModelAnswer


class ChannelHistory(Protocol):
    """Reading a channel. Every method raises ChannelAccessError when Slack says the bot may not do it."""

    def find_channel_id(self, name: str, token: str) -> str | None:
        """The ID of the public or private channel called `name` that the bot can see, or None."""

    def get_channel_info(self, channel_id: str, token: str) -> ChannelInfo:
        """What the channel is and whether the bot is in it."""

    def fetch(
        self, channel_id: str, oldest: float, latest: str | None, limit: int, with_replies: bool, token: str, replies_until: float | None = None
    ) -> HistoryBatch:
        """Up to `limit` top-level messages newer than `oldest` and older than `latest` (a Slack ts, exclusive; None means now),
        newest first, noise (joins, topic changes) dropped. With `with_replies` each message that has a thread carries its replies,
        those posted after `replies_until` (the end of the window, a Unix time) left out. `messages` may be built as it is read, so
        it is read once, in order."""

    def is_member(self, channel_id: str, user_id: str, token: str, max_pages: int) -> bool | None:
        """Whether `user_id` is in the channel; None if not found within `max_pages` pages of members."""

    def can_read_files(self, channel_id: str, token: str) -> bool:
        """Whether the bot's token may list and read files."""


class ChannelFiles(Protocol):
    def describe(self, file_id: str, token: str) -> FileMeta:
        """What Slack says about a file.

        Raises:
            ChannelAnalysisError: no such file.
            ChannelAccessError: the bot may not read files.
        """

    def text(self, file_id: str, token: str) -> str:
        """The file's content as text.

        Raises:
            FileUnreadableError: unsupported type, no text layer, damaged or too large.
        """


class ExtractionModel(Protocol):
    def complete_json(self, system: str, user: str, timeout_seconds: float) -> ModelAnswer:
        """Ask the model for one JSON object. It has no tools and sees only `system` and `user`.

        Raises:
            ExtractionShapeError: the answer is not a JSON object (or was cut off); the caller may ask once more.
            ExtractionError: the call failed or took longer than `timeout_seconds`.
        """
