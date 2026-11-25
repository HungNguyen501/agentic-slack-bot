"""Constants and builders the channel analysis tests share."""
from bots.core.models import BotConfig
from channel_analysis.core.models import ChannelInfo, ChannelMessage

NOW = 1_800_000_000.0
WINDOW_START = f"{NOW - 30 * 86400:.6f}"
BOT = BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret")
CHANNEL = "C0123456789"


def ts_ago(hours: float) -> str:
    return f"{NOW - hours * 3600:.6f}"


def resume_at(hours_ago: float, window_start: str = WINDOW_START) -> str:
    """The `next_before` of a result that stopped at the message `hours_ago` hours old, for a window starting at `window_start`."""
    return f"{ts_ago(hours_ago)}@{window_start}"


def make_message(hours_ago: float, text: str = "report", **overrides) -> ChannelMessage:
    fields = {"ts": ts_ago(hours_ago), "author": "Mail Report", "text": text} | overrides
    return ChannelMessage(**fields)


def make_info(**overrides) -> ChannelInfo:
    fields = {"is_private": False, "is_archived": False, "is_member": True, "is_dm": False} | overrides
    return ChannelInfo(**fields)
