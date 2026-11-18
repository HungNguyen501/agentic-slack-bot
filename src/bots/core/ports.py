from typing import Protocol

from bots.core.models import BotConfig


class BotRepository(Protocol):
    def get_by_app_id(self, app_id: str) -> BotConfig | None:
        """The active bot for a Slack app ID, or None if there is none."""

    def get_by_id(self, bot_id: str) -> BotConfig:
        """The active bot with this id.

        Raises:
            BotNotFoundError: there is no such active bot.
        """
