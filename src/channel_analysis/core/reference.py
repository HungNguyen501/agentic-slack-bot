import re
from dataclasses import dataclass

from channel_analysis.core.errors import ACCESS_MESSAGES, ChannelAnalysisError

_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{8,}$")
_DM_ID = re.compile(r"^D[A-Z0-9]{8,}$")
# A copied channel link (https://acme.slack.com/archives/C0123456789/p17...) or the app's (https://app.slack.com/client/T0123/C0123456789).
_URL = re.compile(r"^https?://[A-Za-z0-9.-]+\.slack\.com/(?:archives|client/[A-Z0-9]+)/([A-Z][A-Z0-9]{8,})(?:[/?#].*)?$")


@dataclass(frozen=True)
class ChannelReference:
    """A channel as the requester wrote it: an ID, or a name to look up."""

    channel_id: str | None
    name: str | None


def normalize_channel_name(name: str) -> str:
    """The form Slack stores a channel name in: lower case, no `#`, words joined with hyphens."""
    return "-".join(name.strip().lstrip("#").lower().split())


def parse_channel_reference(text: str) -> ChannelReference:
    """Read `C123`, `<#C123|name>`, a Slack channel URL (also as `<url|label>`), `#name` or `name`.

    Raises:
        ChannelAnalysisError: the text is empty or names a direct message.
    """
    value = (text or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].split("|", 1)[0]
    url = _URL.match(value)
    candidate = url.group(1) if url else value.lstrip("#")
    if _DM_ID.match(candidate):
        raise ChannelAnalysisError(ACCESS_MESSAGES["is_dm"])
    if _CHANNEL_ID.match(candidate):
        return ChannelReference(candidate, None)
    name = normalize_channel_name(value)
    if not name:
        raise ChannelAnalysisError("Tell me which channel: a link, an ID or a name.")
    return ChannelReference(None, name)
