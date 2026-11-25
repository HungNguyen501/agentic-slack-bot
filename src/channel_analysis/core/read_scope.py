"""Which channels a run may read once it has read channel content (pure)."""
import re

from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.reference import ChannelReference, normalize_channel_name, parse_channel_reference

# Every tool that takes a `channel` argument and reads through the admin's access.
CHANNEL_TOOLS = frozenset({"check_channel_access", "read_channel_messages", "read_channel_file", "analyze_channel"})
_CHANNEL_ID = re.compile(r"\b[CG][A-Z0-9]{8,}\b")
# The line a check starts its answer with, naming the channel it resolved. The agent trusts only the ID on this line: the lines
# after it quote Slack's own error details.
ACCESS_CHECK_PREFIX = "Access check for "
_CHECKED_CHANNEL = re.compile(rf"^{ACCESS_CHECK_PREFIX}([CG][A-Z0-9]{{8,}})\b")
# How a request names a channel on purpose: `#name` or a mention `<#C123|name>`. A bare word that happens to be a channel's name
# ("billing complaints") is a topic, not a choice of channel.
_HASH_NAME = re.compile(r"(?<![\w<&])#([\w-]+)")
_MENTION_NAME = re.compile(r"<#[CG][A-Z0-9]{8,}\|([^>]+)>")


class ReadScope:
    """The channels the person's own request points at.

    Text in a channel is not the admin's, and a crafted post can ask the agent to read another channel, one the admin may see and
    the post's audience may not, and have the answer posted back where the post is. So until channel content has been read the
    channels the agent names are taken as the request's (the model has seen nothing but the request), and after that a channel
    is allowed only if the request points at it (an ID, a link, `#name` or a mention: not a bare word), it is the channel the
    request was made in, or it was named before the first read.
    """

    def __init__(self, request: str, current_channel: str | None) -> None:
        self._ids = set(_CHANNEL_ID.findall(request))
        if current_channel:
            self._ids.add(current_channel)
        # `#42` is an issue or PR number far more often than a channel, so a number alone is not a channel name.
        hashed = (name for name in _HASH_NAME.findall(request) if not name.isdigit())
        self._names = {normalize_channel_name(name) for name in (*hashed, *_MENTION_NAME.findall(request))}

    def trust(self, channel: str | None) -> None:
        """Take `channel`, as the agent named it before any channel content was read."""
        ref = _reference(channel)
        if ref is None:
            return
        if ref.channel_id:
            self._ids.add(ref.channel_id)
        elif ref.name:
            self._names.add(ref.name)

    def trust_resolved(self, check_result: str) -> None:
        """Take the channel an access check says it checked (its first line), so the agent can read by ID a channel it checked by
        name. Only the result of a check that ran goes here: it holds no message or file content."""
        checked = _CHECKED_CHANNEL.match(check_result)
        if checked:
            self._ids.add(checked.group(1))

    def permits(self, channel: str | None) -> bool:
        ref = _reference(channel)
        if ref is None:
            return False
        return ref.channel_id in self._ids if ref.channel_id else (ref.name or "") in self._names


def _reference(channel: str | None) -> ChannelReference | None:
    try:
        return parse_channel_reference(channel or "")
    except ChannelAnalysisError:
        return None
