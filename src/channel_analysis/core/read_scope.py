"""Which channels a run may read once it has read channel content (pure)."""
import re

from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.reference import normalize_channel_name, parse_channel_reference

# Every tool that takes a `channel` argument and reads through the admin's access.
CHANNEL_TOOLS = frozenset({"check_channel_access", "read_channel_messages", "read_channel_file", "analyze_channel"})
_CHANNEL_ID = re.compile(r"\b[CG][A-Z0-9]{8,}\b")


class ReadScope:
    """The channels the person's own request points at.

    Text in a channel is not the admin's, and a crafted post can ask the agent to read another channel, one the admin may see and
    the post's audience may not, and have the answer posted back where the post is. So until channel content has been read the
    channels the agent names are taken as the request's (the model has seen nothing but the request), and after that a channel
    is allowed only if the request names it, it is the channel the request was made in, or it was named before the first read.
    """

    def __init__(self, request: str, current_channel: str | None) -> None:
        self._request = request.lower()
        self._ids = set(_CHANNEL_ID.findall(request))
        if current_channel:
            self._ids.add(current_channel)
        self._names: set[str] = set()

    def trust(self, channel: str | None, result: str = "") -> None:
        """Take `channel`, as the agent named it before any channel content was read, and the channel IDs a check says it resolved to."""
        self._add(channel)
        self._ids.update(_CHANNEL_ID.findall(result))

    def permits(self, channel: str | None) -> bool:
        try:
            ref = parse_channel_reference(channel or "")
        except ChannelAnalysisError:
            return False
        if ref.channel_id:
            return ref.channel_id in self._ids
        name = ref.name or ""
        return name in self._names or re.search(rf"(?<![\w-])#?{re.escape(name)}(?![\w-])", self._request) is not None

    def _add(self, channel: str | None) -> None:
        try:
            ref = parse_channel_reference(channel or "")
        except ChannelAnalysisError:
            return
        if ref.channel_id:
            self._ids.add(ref.channel_id)
        elif ref.name:
            self._names.add(normalize_channel_name(ref.name))
